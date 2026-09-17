#!/usr/bin/env bash
# Shared NIC selection. Sourced by inspect.sh and setup-network.sh so the two can
# never disagree about which port is which.
#
#   pick_nics            -> sets ARM_NIC, LAN_NIC, DEFAULT_NIC, NIC_WHY
#                           returns 0 on success, 1 if it cannot decide (NIC_WHY says why)
#
# Honours overrides set by the caller before the call:
#   ARM_NIC_OVERRIDE, LAN_NIC_OVERRIDE   explicit interface names
#   NIC_FORCE=1                          allow the arm NIC to be the default-route NIC
#
# Boxes in the fleet come in two shapes and the rule differs:
#
#   one onboard NIC + USB adapter   arm = onboard, lan = USB      (UDM 90, older Meerkats)
#   two onboard NICs, no adapter    arm = the onboard NIC that is NOT carrying the
#                                   default route; lan = the one that is
#
# The second case is why this is not just "first PCIe NIC wins": on a two-port box the
# first PCIe NIC by name is usually the one with the office LAN on it, and handing that
# to the arm profile (static, never-default) takes the box off the network entirely.

_nic_bus() { ethtool -i "$1" 2>/dev/null | awk -F': ' '/^bus-info/{print $2}'; }

# Interface carrying the current IPv4 default route, if any.
_default_nic() { ip -4 route show default 2>/dev/null | awk '/^default/{print $5; exit}'; }

pick_nics() {
  ARM_NIC=""; LAN_NIC=""; NIC_WHY=""
  DEFAULT_NIC="$(_default_nic)"

  local pci=() usb=() i bus
  for d in /sys/class/net/*; do
    i=$(basename "$d")
    [[ "$i" == lo || "$i" == wl* ]] && continue
    bus="$(_nic_bus "$i")"
    case "$bus" in
      0000:*) pci+=("$i") ;;
      usb-*)  usb+=("$i") ;;
    esac
  done

  # Explicit overrides win outright - the human at the machine can always be decisive.
  # Naming one port is enough; the other is inferred from what is left.
  if [[ -n "${ARM_NIC_OVERRIDE:-}" || -n "${LAN_NIC_OVERRIDE:-}" ]]; then
    ARM_NIC="${ARM_NIC_OVERRIDE:-}"; LAN_NIC="${LAN_NIC_OVERRIDE:-}"
    local n
    if [[ -z "$LAN_NIC" ]]; then
      # Prefer the port that already has the office LAN on it.
      if [[ -n "$DEFAULT_NIC" && "$DEFAULT_NIC" != "$ARM_NIC" ]]; then
        LAN_NIC="$DEFAULT_NIC"
      else
        for n in "${usb[@]}" "${pci[@]}"; do [[ "$n" != "$ARM_NIC" ]] && { LAN_NIC="$n"; break; }; done
      fi
    fi
    if [[ -z "$ARM_NIC" ]]; then
      for n in "${pci[@]}"; do [[ "$n" != "$LAN_NIC" ]] && { ARM_NIC="$n"; break; }; done
    fi
    NIC_WHY="explicit override -> lan=$LAN_NIC arm=$ARM_NIC"
  elif [[ ${#usb[@]} -gt 0 ]]; then
    # Classic layout: the USB adapter is the office LAN, the onboard port is the arm.
    [[ ${#pci[@]} -gt 0 ]] || { NIC_WHY="no onboard PCIe NIC found"; return 1; }
    LAN_NIC="${usb[0]}"; ARM_NIC="${pci[0]}"
    NIC_WHY="USB adapter present -> lan=USB, arm=onboard"
  elif [[ ${#pci[@]} -ge 2 ]]; then
    # Two-port box. The default route tells us which port the office LAN is on.
    if [[ -z "$DEFAULT_NIC" ]]; then
      NIC_WHY="two onboard NICs but no default route, so the office LAN port cannot be
  identified. Plug the office LAN in and re-run, or name the ports explicitly:
    sudo bash scripts/setup-network.sh --lan-nic <iface> --arm-nic <iface>"
      return 1
    fi
    local is_pci=0 n
    for n in "${pci[@]}"; do [[ "$n" == "$DEFAULT_NIC" ]] && is_pci=1; done
    if [[ $is_pci -eq 0 ]]; then
      NIC_WHY="default route is on '$DEFAULT_NIC', which is not one of the onboard NICs.
  Name the ports explicitly: --lan-nic <iface> --arm-nic <iface>"
      return 1
    fi
    LAN_NIC="$DEFAULT_NIC"
    for n in "${pci[@]}"; do [[ "$n" != "$LAN_NIC" ]] && { ARM_NIC="$n"; break; }; done
    NIC_WHY="two onboard NICs -> lan=$LAN_NIC (has the default route), arm=$ARM_NIC"
  else
    NIC_WHY="only one network port and no USB adapter - plug in the LAN adapter,
  or use a box with a second ethernet port"
    return 1
  fi

  [[ -n "$ARM_NIC" ]] || { NIC_WHY="could not determine the arm NIC"; return 1; }
  [[ -n "$LAN_NIC" ]] || { NIC_WHY="could not determine the LAN NIC"; return 1; }
  [[ -e "/sys/class/net/$ARM_NIC" ]] || { NIC_WHY="no such interface: $ARM_NIC"; return 1; }
  [[ -e "/sys/class/net/$LAN_NIC" ]] || { NIC_WHY="no such interface: $LAN_NIC"; return 1; }
  [[ "$ARM_NIC" != "$LAN_NIC" ]] || { NIC_WHY="arm and lan NIC are the same interface"; return 1; }

  # The guard that matters: the arm profile is static + never-default. Putting it on the
  # interface that currently carries the default route disconnects the box, and on a
  # machine with no wifi there is nothing to fail over to.
  if [[ -n "$DEFAULT_NIC" && "$ARM_NIC" == "$DEFAULT_NIC" && "${NIC_FORCE:-0}" != "1" ]]; then
    NIC_WHY="refusing: arm NIC '$ARM_NIC' is carrying the default route. Applying the
  static never-default arm profile to it would take this box off the network.
  Use --arm-nic/--lan-nic to correct the choice, or --force if you are certain."
    return 1
  fi
  return 0
}
