"""Port selection in scripts/lib/pick-nics.sh, run against fake interface tables.

`pick_nics` reads sysfs and ethtool; `_pick_nics_decide` is the rule on its own. It takes
a table of "iface bus carrier" lines plus the default-route interface, so every box
layout the farm has met can be replayed here without the hardware.
"""
import pathlib
import subprocess

LIB = pathlib.Path(__file__).parent / "lib" / "pick-nics.sh"

# bus-info as ethtool reports it: onboard ports are PCI addresses, adapters are usb-*.
ONBOARD_A = ("enp85s0", "0000:55:00.0")
ONBOARD_B = ("enp86s0", "0000:56:00.0")
USB = ("enx00e04c680001", "usb-0000:00:14.0-2")


def table(*rows):
    return "\n".join(f"{i} {bus} {carrier}" for (i, bus), carrier in rows)


def decide(rows, default="", arm=None, lan=None, force=False):
    """Run the rule under `set -u` (inspect.sh's mode). Returns (rc, arm, lan, why)."""
    script = f"""
set -u
. "{LIB}"
ARM_NIC_OVERRIDE="{arm or ''}"; LAN_NIC_OVERRIDE="{lan or ''}"; NIC_FORCE={1 if force else 0}
_pick_nics_decide "$TABLE" "$DEFAULT"
rc=$?
printf '%s\\n%s\\n%s\\n%s' "$rc" "$ARM_NIC" "$LAN_NIC" "$NIC_WHY"
"""
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env={"TABLE": table(*rows), "DEFAULT": default, "PATH": "/usr/bin:/bin"})
    assert r.stderr == "", r.stderr
    rc, arm_nic, lan_nic, why = r.stdout.split("\n", 3)
    return int(rc), arm_nic, lan_nic, why


# --- one onboard port + USB adapter (older boxes) ---

def test_usb_adapter_is_lan_onboard_is_arm():
    rc, arm, lan, _ = decide([(ONBOARD_A, 1), (USB, 1)], default=USB[0])
    assert (rc, arm, lan) == (0, ONBOARD_A[0], USB[0])


def test_usb_adapter_is_lan_while_arm_is_unplugged():
    rc, arm, lan, _ = decide([(ONBOARD_A, 0), (USB, 1)], default=USB[0])
    assert (rc, arm, lan) == (0, ONBOARD_A[0], USB[0])


def test_refuses_arm_on_default_route_with_one_onboard_port():
    # LAN cable in the onboard port, adapter unplugged: the only onboard port carries
    # the default route, so making it the arm would take the box off the network.
    rc, _, _, why = decide([(ONBOARD_A, 1), (USB, 0)], default=ONBOARD_A[0])
    assert rc == 1 and "refusing" in why


# --- two onboard ports, no adapter (newer Meerkats) ---

def test_two_onboard_ports_lan_is_default_route():
    rc, arm, lan, _ = decide([(ONBOARD_A, 1), (ONBOARD_B, 1)], default=ONBOARD_B[0])
    assert (rc, arm, lan) == (0, ONBOARD_A[0], ONBOARD_B[0])
    rc, arm, lan, _ = decide([(ONBOARD_A, 1), (ONBOARD_B, 1)], default=ONBOARD_A[0])
    assert (rc, arm, lan) == (0, ONBOARD_B[0], ONBOARD_A[0])


def test_two_onboard_ports_without_default_route_stops():
    rc, _, _, why = decide([(ONBOARD_A, 1), (ONBOARD_B, 1)], default="")
    assert rc == 1 and "no default route" in why


# --- two onboard ports plus an unplugged USB adapter (armfarm14, 2026-11-20) ---

def test_armfarm14_unplugged_adapter_is_not_the_lan():
    # armfarm14: arm on enp85s0, office LAN on enp86s0, a USB adapter with no cable.
    rows = [(ONBOARD_A, 1), (ONBOARD_B, 1), (USB, 0)]
    rc, arm, lan, why = decide(rows, default=ONBOARD_B[0])
    assert (rc, arm, lan) == (0, ONBOARD_A[0], ONBOARD_B[0]), why


def test_unplugged_adapter_default_on_first_onboard_port():
    # Same box with the cables the other way round. The old rule chose arm=enp85s0
    # (the LAN port) and refused; the onboard port with the default route is the LAN.
    rows = [(ONBOARD_A, 1), (ONBOARD_B, 1), (USB, 0)]
    rc, arm, lan, why = decide(rows, default=ONBOARD_A[0])
    assert (rc, arm, lan) == (0, ONBOARD_B[0], ONBOARD_A[0]), why


def test_unplugged_adapter_and_arm_not_cabled_yet():
    # The arm cable is often not in yet when networking is set up.
    rows = [(ONBOARD_A, 1), (ONBOARD_B, 0), (USB, 0)]
    rc, arm, lan, why = decide(rows, default=ONBOARD_A[0])
    assert (rc, arm, lan) == (0, ONBOARD_B[0], ONBOARD_A[0]), why


def test_adapter_with_carrier_still_wins_as_lan():
    rows = [(ONBOARD_A, 0), (ONBOARD_B, 0), (USB, 1)]
    rc, arm, lan, _ = decide(rows, default=USB[0])
    assert (rc, arm, lan) == (0, ONBOARD_A[0], USB[0])


# --- overrides and the default-route guard ---

def test_override_arm_infers_lan_from_default_route():
    rows = [(ONBOARD_A, 1), (ONBOARD_B, 1), (USB, 0)]
    rc, arm, lan, _ = decide(rows, default=ONBOARD_B[0], arm=ONBOARD_A[0])
    assert (rc, arm, lan) == (0, ONBOARD_A[0], ONBOARD_B[0])


def test_override_onto_default_route_is_refused_unless_forced():
    rows = [(ONBOARD_A, 1), (ONBOARD_B, 1)]
    rc, _, _, why = decide(rows, default=ONBOARD_A[0], arm=ONBOARD_A[0], lan=ONBOARD_B[0])
    assert rc == 1 and "refusing" in why
    rc, arm, _, _ = decide(rows, default=ONBOARD_A[0], arm=ONBOARD_A[0], lan=ONBOARD_B[0],
                           force=True)
    assert (rc, arm) == (0, ONBOARD_A[0])


def test_override_unknown_interface():
    rc, _, _, why = decide([(ONBOARD_A, 1), (USB, 1)], default=USB[0], arm="eth9")
    assert rc == 1 and "no such interface: eth9" in why


def test_single_port_no_adapter():
    rc, _, _, why = decide([(ONBOARD_A, 1)], default=ONBOARD_A[0])
    assert rc == 1 and "only one network port" in why
