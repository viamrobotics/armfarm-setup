#!/usr/bin/env bash
# Arm link and office LAN, on whichever ports this box actually has.
#
#   sudo bash setup-network.sh
#   sudo bash setup-network.sh --dry-run                  # show the plan, change nothing
#   sudo bash setup-network.sh --arm-nic enp87s0          # name a port explicitly
#   sudo bash setup-network.sh --lan-nic enp86s0 --force  # override the safety guard
#
# Port selection lives in scripts/lib/pick-nics.sh and handles both fleet shapes:
# one onboard NIC + USB adapter, and two onboard NICs with no adapter. See that file.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$here/lib/pick-nics.sh"

ARM_HOST_IP="192.168.1.150/24"
LAN_METRIC=100          # lower than wifi -> wired is default route, wifi auto-fails over
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --arm-nic) ARM_NIC_OVERRIDE="$2"; shift 2 ;;
    --lan-nic) LAN_NIC_OVERRIDE="$2"; shift 2 ;;
    --force)   NIC_FORCE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# Every mutating command goes through this. In dry-run it is printed, not run -
# reconfiguring the port the box is reachable over is worth being able to preview.
# It owns the output redirection so the call sites stay readable.
run() {
  if [[ $DRY_RUN -eq 1 ]]; then printf '  would run:'; printf ' %q' "$@"; printf '\n'; return 0; fi
  "$@" >/dev/null 2>&1
}

if ! pick_nics; then
  echo "cannot choose network ports:" >&2
  echo "  $NIC_WHY" >&2
  exit 1
fi
arm_mac=$(cat "/sys/class/net/$ARM_NIC/address")
lan_mac=$(cat "/sys/class/net/$LAN_NIC/address")
lan_bus="$(_nic_bus "$LAN_NIC")"
echo "== arm NIC: $ARM_NIC ($arm_mac)   lan NIC: $LAN_NIC ($lan_mac) =="
echo "   $NIC_WHY"
[[ $DRY_RUN -eq 1 ]] && echo "   DRY RUN - nothing will be changed"

# Reconfiguring the LAN port drops its connection for a moment. Harmless on a console,
# not harmless if this is the link you are logged in over.
if [[ $DRY_RUN -eq 0 && "$LAN_NIC" == "${DEFAULT_NIC:-}" && -n "${SSH_CONNECTION:-}" ]]; then
  echo
  echo "   NOTE: you are connected over ssh and the office LAN port is being"
  echo "   reconfigured. Expect a brief drop; it should come back on the same DHCP"
  echo "   address. Run this from the machine's console if you would rather not risk it."
  echo
fi

# Arm profile: bound BY MAC so a rename or adapter swap can never orphan it.
# The higher autoconnect-priority is what stops the LAN profile from claiming the
# arm port on a box where both ports look alike.
run nmcli connection delete "xArm" || true
run nmcli connection add type ethernet con-name "xArm" ifname "" \
  802-3-ethernet.mac-address "$arm_mac" \
  ipv4.method manual ipv4.addresses "$ARM_HOST_IP" ipv4.never-default yes \
  connection.autoconnect yes connection.autoconnect-priority 10

# LAN profile. If the LAN is on a USB adapter, leave it unbound so ANY adapter picks
# it up. If it is an onboard port, bind it by MAC - there is no adapter to swap, and
# an unbound profile on a two-port box is just something else that can grab the arm NIC.
run nmcli connection delete "office-lan" || true
lan_bind=()
case "$lan_bus" in
  usb-*) echo "   lan profile: unbound (USB adapter - any adapter will pick it up)" ;;
  *)     lan_bind=(802-3-ethernet.mac-address "$lan_mac")
         echo "   lan profile: bound to $lan_mac (onboard port)" ;;
esac
run nmcli connection add type ethernet con-name "office-lan" ifname "" \
  "${lan_bind[@]}" \
  ipv4.method auto ipv4.never-default no \
  ipv4.route-metric $LAN_METRIC ipv6.route-metric $LAN_METRIC \
  connection.autoconnect yes connection.autoconnect-priority 0

# Bring the LAN up FIRST, so the box takes its new profile over from the stale one
# without an interval where it has no route at all.
run nmcli connection up "office-lan" ifname "$LAN_NIC"

# The arm link often has no cable yet at this point; that is not a failure.
if ! run nmcli connection up "xArm" ifname "$ARM_NIC"; then
  echo "   note: could not activate the arm profile on $ARM_NIC"
  [[ "$(cat "/sys/class/net/$ARM_NIC/carrier" 2>/dev/null || echo 0)" == "1" ]] \
    || echo "   no carrier on $ARM_NIC - the profile is saved and will come up when the arm is cabled."
fi

# Only now disable the stale auto-created profiles, so nothing fights for these ports
# on the next boot. Done last: until this point one of them may be carrying the LAN.
# Split on ':' rather than whitespace - profile names contain spaces
# ("Ethernet connection 1" is the usual auto-created one).
while IFS=: read -r name dev ctype; do
  [[ "$ctype" == "802-3-ethernet" ]] || continue
  [[ "$name" == "xArm" || "$name" == "office-lan" ]] && continue
  # Profiles on our two ports, and unbound generic profiles - an idle generic profile
  # is exactly what grabs the arm NIC later and DHCP-loops on it.
  [[ "$dev" == "$ARM_NIC" || "$dev" == "$LAN_NIC" || -z "$dev" || "$dev" == "--" ]] || continue
  echo "  disabling stale profile: $name"
  run nmcli connection modify "$name" connection.autoconnect no || true
  run nmcli connection down "$name" || true
done < <(nmcli -t -f NAME,DEVICE,TYPE connection show)

if [[ $DRY_RUN -eq 1 ]]; then
  echo
  echo "== dry run - nothing was changed. Re-run without --dry-run to apply. =="
  exit 0
fi

echo "== result =="
ip -br -4 addr show "$ARM_NIC" | sed 's/^/  /'
ip -br -4 addr show "$LAN_NIC" | sed 's/^/  /'
ip -4 route | grep default | sed 's/^/  /'
echo -n "  arm 192.168.1.212: "
ping -c2 -W2 -q 192.168.1.212 >/dev/null 2>&1 && echo "reachable" || echo "UNREACHABLE - check cabling"
