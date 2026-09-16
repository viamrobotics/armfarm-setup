#!/usr/bin/env bash
# Arm link on the onboard PCIe NIC, office LAN on the USB NIC, wifi as fallback.
# Selects NICs BY CAPABILITY so this works on Meerkat and UDM 90 alike.
# Usage: sudo bash setup-network.sh
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }

ARM_HOST_IP="192.168.1.150/24"
LAN_METRIC=100          # lower than wifi -> wired is default route, wifi auto-fails over

arm_nic=""; lan_nic=""
for d in /sys/class/net/*; do
  i=$(basename "$d"); [[ "$i" == lo || "$i" == wl* ]] && continue
  bus=$(ethtool -i "$i" 2>/dev/null | awk -F': ' '/^bus-info/{print $2}')
  case "$bus" in
    0000:*) [[ -z "$arm_nic" ]] && arm_nic="$i" ;;
    usb-*)  [[ -z "$lan_nic" ]] && lan_nic="$i" ;;
  esac
done
[[ -n "$arm_nic" ]] || { echo "no onboard PCIe NIC found"; exit 1; }
[[ -n "$lan_nic" ]] || { echo "no USB NIC found - plug in the LAN adapter"; exit 1; }
arm_mac=$(cat "/sys/class/net/$arm_nic/address")
echo "== arm NIC: $arm_nic ($arm_mac)   lan NIC: $lan_nic =="

# Kill any auto-created generic profile that would grab the arm NIC and DHCP-loop forever.
while read -r name dev; do
  [[ "$dev" == "$arm_nic" || "$dev" == "$lan_nic" ]] || continue
  [[ "$name" == "xArm" || "$name" == "office-lan" ]] && continue
  echo "  disabling stale profile: $name"
  nmcli connection modify "$name" connection.autoconnect no || true
  nmcli connection down "$name" >/dev/null 2>&1 || true
done < <(nmcli -t -f NAME,DEVICE,TYPE connection show | awk -F: '$3=="802-3-ethernet"{print $1" "$2}')

# Arm profile: bound BY MAC so a rename or adapter swap can never orphan it.
nmcli connection delete "xArm" >/dev/null 2>&1 || true
nmcli connection add type ethernet con-name "xArm" ifname "" \
  802-3-ethernet.mac-address "$arm_mac" \
  ipv4.method manual ipv4.addresses "$ARM_HOST_IP" ipv4.never-default yes \
  connection.autoconnect yes connection.autoconnect-priority 10 >/dev/null

# LAN profile: unbound, so ANY usb adapter picks it up.
nmcli connection delete "office-lan" >/dev/null 2>&1 || true
nmcli connection add type ethernet con-name "office-lan" ifname "" \
  ipv4.method auto ipv4.never-default no \
  ipv4.route-metric $LAN_METRIC ipv6.route-metric $LAN_METRIC \
  connection.autoconnect yes connection.autoconnect-priority 0 >/dev/null

nmcli connection up "xArm"       ifname "$arm_nic" >/dev/null
nmcli connection up "office-lan" ifname "$lan_nic" >/dev/null

echo "== result =="
ip -br -4 addr show "$arm_nic" | sed 's/^/  /'
ip -br -4 addr show "$lan_nic" | sed 's/^/  /'
ip -4 route | grep default | sed 's/^/  /'
echo -n "  arm 192.168.1.212: "
ping -c2 -W2 -q 192.168.1.212 >/dev/null 2>&1 && echo "reachable" || echo "UNREACHABLE - check cabling"
