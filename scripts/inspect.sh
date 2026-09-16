#!/usr/bin/env bash
# Gather every fact needed to provision this machine. Read-only: changes nothing.
set -uo pipefail

echo "=== compute ==="
hostnamectl 2>/dev/null | grep -E "Static hostname|Hardware Model|Operating System" | sed 's/^ */  /'

echo
echo "=== NICs (by capability, never by name) ==="
ARM_NIC=""; LAN_NIC=""
for d in /sys/class/net/*; do
  i=$(basename "$d"); [[ "$i" == lo || "$i" == wl* ]] && continue
  bus=$(ethtool -i "$i" 2>/dev/null | awk -F': ' '/^bus-info/{print $2}')
  drv=$(ethtool -i "$i" 2>/dev/null | awk -F': ' '/^driver/{print $2}')
  mac=$(cat "$d/address" 2>/dev/null)
  car=$(cat "$d/carrier" 2>/dev/null || echo 0)
  case "$bus" in
    0000:*) role="ARM  (onboard PCIe)"; [[ -z "$ARM_NIC" ]] && ARM_NIC="$i" ;;
    usb-*)  role="LAN  (USB)";          [[ -z "$LAN_NIC" ]] && LAN_NIC="$i" ;;
    *)      role="?" ;;
  esac
  printf "  %-18s %-20s driver=%-10s mac=%s carrier=%s\n" "$i" "$role" "$drv" "$mac" "$car"
done
echo "  -> arm NIC: ${ARM_NIC:-NONE}   lan NIC: ${LAN_NIC:-NONE}"

echo
echo "=== arm controller reachable? ==="
if ping -c1 -W2 192.168.1.212 >/dev/null 2>&1; then
  echo "  192.168.1.212 OK ($(ping -c2 -W2 -q 192.168.1.212 2>/dev/null | awk -F'/' '/rtt/{print $5" ms avg"}'))"
else
  echo "  192.168.1.212 UNREACHABLE (expected before network setup)"
fi

echo
echo "=== RealSense ==="
lsusb 2>/dev/null | grep -i "realsense" | sed 's/^/  /' || echo "  none detected"
echo "  NOTE: sysfs 'serial' is the ASIC serial, NOT the device serial the driver matches on."
if command -v rs-enumerate-devices >/dev/null; then
  rs-enumerate-devices -s 2>/dev/null | sed 's/^/  /' | head -5
else
  echo "  rs-enumerate-devices not installed - read the device serial from viam-server's"
  echo "  startup log after first apply, or install librealsense2-utils."
fi

echo
echo "=== ssh ==="
echo "  enabled=$(systemctl is-enabled ssh 2>&1)  active=$(systemctl is-active ssh 2>&1)"
ss -lntp 2>/dev/null | grep -q ':22 ' && echo "  listening on 22" || echo "  NOT listening on 22"
systemctl is-active ufw >/dev/null 2>&1 && echo "  ufw ACTIVE - confirm 22 allowed (needs sudo: ufw status)"

echo
echo "=== viam-agent ==="
echo "  $(systemctl is-active viam-agent 2>&1) / $(systemctl is-enabled viam-agent 2>&1)"
[[ -f /etc/viam.json ]] && python3 -c "
import json;c=json.load(open('/etc/viam.json'))['cloud']
print('  part id:',c.get('id'),' auth:',','.join(k for k in c if k in ('secret','api_key')))" || echo "  no /etc/viam.json"
