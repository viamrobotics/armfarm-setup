#!/usr/bin/env bash
# Arm link and venue LAN, on whichever ports this box has. Hackathon copy of the HQ
# scripts/setup-network.sh: same port logic (shared scripts/lib/pick-nics.sh), different
# profile names, plus a wifi-aware fallback and a subnet-clash check. Kept separate so
# the HQ flow can change without touching this one.
#
#   sudo bash setup-network.sh                       # apply
#        bash setup-network.sh --dry-run             # print the plan, no root needed
#   sudo bash setup-network.sh --arm-ip 192.168.1.233
#   sudo bash setup-network.sh --arm-nic enp87s0 --lan-nic enp86s0
#   sudo bash setup-network.sh --lan-nic enp86s0 --force
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
. "$root/scripts/lib/pick-nics.sh"

venue_file="$(ls "$here"/../venues/*.json | head -1)"
DRY_RUN=0; ARM_IP=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --arm-nic) ARM_NIC_OVERRIDE="$2"; shift 2 ;;
    --lan-nic) LAN_NIC_OVERRIDE="$2"; shift 2 ;;
    --force)   NIC_FORCE=1; shift ;;
    --arm-ip)  ARM_IP="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --venue-file) venue_file="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# Root is needed to APPLY. --dry-run only reads (nmcli show, ip, sysfs), so anyone may
# preview the plan. The check sits after parsing for exactly that reason - when it ran
# first, you could not even see the plan for a box without becoming root.
if [[ $DRY_RUN -eq 0 && $EUID -ne 0 ]]; then
  echo "run with sudo (or --dry-run to preview without changing anything)" >&2
  exit 1
fi

vj() { python3 -c 'import json,sys;d=json.load(open(sys.argv[1]))
for k in sys.argv[2].split("."): d=d[k]
print(d)' "$venue_file" "$1"; }
ARM_HOST_IP="$(vj network.arm_host_ip_cidr)"
ARM_SUBNET="$(vj network.arm_subnet)"
LAN_METRIC="$(vj network.lan_route_metric)"
LAN_PROFILE="$(vj network.lan_profile_name)"
[[ -n "$ARM_IP" ]] || ARM_IP="$(vj network.arm_default_controller_ip)"

run() {
  if [[ $DRY_RUN -eq 1 ]]; then printf '  would run:'; printf ' %q' "$@"; printf '\n'; return 0; fi
  "$@" >/dev/null 2>&1
}

# If wifi already holds the default route (box was joined to wifi before this ran),
# pick-nics cannot use "the port with the default route" to find the LAN. Fall back
# to: the onboard port that has carrier and an IPv4 address.
if [[ -z "${LAN_NIC_OVERRIDE:-}" && -z "${ARM_NIC_OVERRIDE:-}" ]]; then
  dn="$(_default_nic)"
  if [[ "$dn" == wl* ]]; then
    for d in /sys/class/net/*; do
      i=$(basename "$d"); [[ "$i" == lo || "$i" == wl* ]] && continue
      [[ "$(_nic_bus "$i")" == 0000:* ]] || continue
      [[ "$(cat "$d/carrier" 2>/dev/null)" == 1 ]] || continue
      ip -4 addr show "$i" | grep -q 'inet ' || continue
      LAN_NIC_OVERRIDE="$i"
      echo "   default route is on wifi ($dn); taking $i as the LAN port (onboard, carrier, has an address)"
      break
    done
  fi
fi

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
echo "   arm controller expected at $ARM_IP (per-machine; --arm-ip to change)"
[[ $DRY_RUN -eq 1 ]] && echo "   DRY RUN - nothing will be changed"

if [[ $DRY_RUN -eq 0 && "$LAN_NIC" == "${DEFAULT_NIC:-}" && -n "${SSH_CONNECTION:-}" ]]; then
  echo
  echo "   NOTE: you are on ssh over the port being reconfigured. Expect a brief drop;"
  echo "   it should come back on the same DHCP address."
  echo
fi

# Arm profile: bound BY MAC, static, never-default, high autoconnect priority so the
# generic DHCP profile can never grab this port and DHCP-loop on it.
run nmcli connection delete "xArm" || true
run nmcli connection add type ethernet con-name "xArm" ifname "" \
  802-3-ethernet.mac-address "$arm_mac" \
  ipv4.method manual ipv4.addresses "$ARM_HOST_IP" ipv4.never-default yes \
  connection.autoconnect yes connection.autoconnect-priority 10

# LAN profile. USB adapter -> unbound so any adapter works; onboard -> bound by MAC.
run nmcli connection delete "$LAN_PROFILE" || true
run nmcli connection delete "office-lan" || true    # HQ profile on a borrowed box
lan_bind=()
case "$lan_bus" in
  usb-*) echo "   lan profile: unbound (USB adapter)" ;;
  *)     lan_bind=(802-3-ethernet.mac-address "$lan_mac")
         echo "   lan profile: bound to $lan_mac (onboard port)" ;;
esac
run nmcli connection add type ethernet con-name "$LAN_PROFILE" ifname "" \
  "${lan_bind[@]}" \
  ipv4.method auto ipv4.never-default no \
  ipv4.route-metric "$LAN_METRIC" ipv6.route-metric "$LAN_METRIC" \
  connection.autoconnect yes connection.autoconnect-priority 0

run nmcli connection up "$LAN_PROFILE" ifname "$LAN_NIC"

if ! run nmcli connection up "xArm" ifname "$ARM_NIC"; then
  echo "   note: could not activate the arm profile on $ARM_NIC"
  [[ "$(cat "/sys/class/net/$ARM_NIC/carrier" 2>/dev/null || echo 0)" == "1" ]] \
    || echo "   no carrier on $ARM_NIC - saved; it comes up when the arm is cabled."
fi

# Disable stale auto-created ethernet profiles so nothing fights for these ports.
while IFS=: read -r pname dev ctype; do
  [[ "$ctype" == "802-3-ethernet" ]] || continue
  [[ "$pname" == "xArm" || "$pname" == "$LAN_PROFILE" ]] && continue
  [[ "$dev" == "$ARM_NIC" || "$dev" == "$LAN_NIC" || -z "$dev" || "$dev" == "--" ]] || continue
  echo "  disabling stale profile: $pname"
  run nmcli connection modify "$pname" connection.autoconnect no || true
  run nmcli connection down "$pname" || true
done < <(nmcli -t -f NAME,DEVICE,TYPE connection show)

if [[ $DRY_RUN -eq 1 ]]; then
  echo
  echo "== dry run - nothing was changed. =="
  exit 0
fi

echo "== result =="
ip -br -4 addr show "$ARM_NIC" | sed 's/^/  /'
ip -br -4 addr show "$LAN_NIC" | sed 's/^/  /'
ip -4 route | grep default | sed 's/^/  /'

# Subnet clash: the LAN must not be on the arm's subnet.
lan_addr="$(ip -4 -o addr show "$LAN_NIC" | awk '{print $4}' | head -1)"
if [[ -n "$lan_addr" && "${lan_addr%.*}" == "${ARM_SUBNET%.*}" ]]; then
  echo
  echo "  *** SUBNET CLASH: venue LAN gave $LAN_NIC $lan_addr, on the arm subnet $ARM_SUBNET ***"
  echo "  *** Arm traffic will route out the wrong port. Change the venue router's subnet.  ***"
fi

echo -n "  arm $ARM_IP: "
ping -c2 -W2 -q "$ARM_IP" >/dev/null 2>&1 && echo "reachable" \
  || echo "not answering - cable, power, or a different address. Run find-arm-ip.sh."
