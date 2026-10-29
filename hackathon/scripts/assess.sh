#!/usr/bin/env bash
# What does this box need? Read-only, no root. Run on the box (console or ssh):
#
#   bash ~/armfarm-setup/hackathon/scripts/assess.sh
#
# Ends with a NEEDS list: the steps still to do for this box, in order.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
venue_file="$(ls "$here"/../venues/*.json | head -1)"
needs=()

vj() { python3 -c 'import json,sys;d=json.load(open(sys.argv[1]))
for k in sys.argv[2].split("."): d=d[k]
print(d)' "$venue_file" "$1" 2>/dev/null; }

echo "=== compute ==="
hostnamectl 2>/dev/null | grep -E "Static hostname|Hardware Model|Operating System" | sed 's/^ */  /'
echo "  user: $(id -un)  groups: $(id -Gn | tr ' ' ',')"
echo "  uptime:$(uptime | sed 's/.*up/ up/')"

echo
echo "=== tooling ==="
missing=()
for t in curl python3 nmcli ethtool avahi-daemon sshd; do
  if command -v "$t" >/dev/null 2>&1; then printf "  %-14s ok\n" "$t"
  else printf "  %-14s MISSING\n" "$t"; missing+=("$t"); fi
done
[[ ${#missing[@]} -gt 0 ]] && needs+=("console-bootstrap (installs: ${missing[*]})")

echo
echo "=== ssh ==="
if ss -lntp 2>/dev/null | grep -q ':22 '; then echo "  listening on 22"
else echo "  NOT listening on 22"; needs+=("console-bootstrap (enables ssh)"); fi
if [[ -s "$HOME/.ssh/authorized_keys" ]]; then
  echo "  authorized_keys: $(grep -c . "$HOME/.ssh/authorized_keys") key(s)"
else
  echo "  authorized_keys: none - the laptop will need ssh-copy-id"
fi

echo
echo "=== sleep ==="
# A box that suspends during the event drops its arm and reads as a network fault.
sleep_state="$(systemctl is-enabled sleep.target 2>&1 || true)"
if [[ "$sleep_state" == "masked" ]]; then
  echo "  sleep.target masked - cannot suspend"
else
  echo "  sleep.target is '$sleep_state' - this box CAN suspend"
  needs+=("no-sleep.sh (mask suspend; console-bootstrap does this too)")
fi
[[ -f /etc/systemd/logind.conf.d/90-hackathon-no-sleep.conf ]] \
  && echo "  logind: lid/idle/sleep keys ignored" || echo "  logind: default handling (no-sleep.sh not run)"

echo
echo "=== network ports ==="
if command -v ethtool >/dev/null 2>&1; then
  . "$root/scripts/lib/pick-nics.sh"
  nic_ok=0; pick_nics && nic_ok=1
  for d in /sys/class/net/*; do
    i=$(basename "$d"); [[ "$i" == lo ]] && continue
    bus=$(ethtool -i "$i" 2>/dev/null | awk -F': ' '/^bus-info/{print $2}')
    mac=$(cat "$d/address" 2>/dev/null); car=$(cat "$d/carrier" 2>/dev/null || echo 0)
    addr=$(ip -4 -o addr show "$i" 2>/dev/null | awk '{print $4}' | head -1)
    case "$i" in wl*) kind="wifi" ;; *) case "$bus" in 0000:*) kind="onboard" ;; usb-*) kind="USB" ;; *) kind="virtual" ;; esac ;; esac
    role="-"
    [[ "$i" == "${ARM_NIC:-}" ]] && role="ARM"
    [[ "$i" == "${LAN_NIC:-}" ]] && role="LAN"
    [[ "$i" == "${DEFAULT_NIC:-}" ]] && role="$role (default route)"
    printf "  %-16s %-20s %-8s carrier=%s addr=%s\n" "$i" "$role" "$kind" "$car" "${addr:-none}"
  done
  if [[ $nic_ok -eq 1 ]]; then echo "  -> arm=$ARM_NIC lan=$LAN_NIC   ($NIC_WHY)"
  else echo "  -> cannot choose ports yet: $NIC_WHY"; fi
else
  echo "  (ethtool missing; port roles unknown until console-bootstrap)"
fi

echo
echo "=== NetworkManager profiles ==="
nmcli -t -f NAME,TYPE,DEVICE connection show 2>/dev/null | awk -F: '{printf "  %-24s %-16s %s\n",$1,$2,($3==""?"(inactive)":$3)}'
have_arm_profile=0; nmcli -t -f NAME connection show 2>/dev/null | grep -qx "xArm" && have_arm_profile=1
lan_profile="$(vj network.lan_profile_name)"
have_lan_profile=0; nmcli -t -f NAME connection show 2>/dev/null | grep -qx "$lan_profile" && have_lan_profile=1
[[ $have_arm_profile -eq 1 && $have_lan_profile -eq 1 ]] || needs+=("console-bootstrap / setup-network (arm + venue LAN profiles)")

echo
echo "=== wifi ==="
# These boxes are wired-only by policy: the venue LAN is the single route and wifi is
# never configured. So an unconnected radio is the EXPECTED state and is never a NEEDS
# item - reporting it as one made every finished box look unfinished. A box that IS on
# wifi is the surprise worth flagging: it is a second default route, and a venue AP
# handing out 192.168.1.x collides with the arm subnet.
wdev="$(nmcli -t -f DEVICE,TYPE dev 2>/dev/null | awk -F: '$2=="wifi"{print $1; exit}')"
if [[ -z "$wdev" ]]; then
  echo "  no wifi device (fine - these boxes are wired-only)"
else
  ssid="$(nmcli -t -f ACTIVE,SSID dev wifi 2>/dev/null | awk -F: '$1=="yes"{print $2; exit}')"
  if [[ -n "$ssid" ]]; then
    echo "  *** $wdev is connected to '$ssid' - these boxes are meant to be wired-only ***"
    needs+=("disconnect wifi on $wdev (wired-only; a venue AP can hand out the arm subnet)")
  else
    echo "  $wdev not connected (expected - wired-only)"
  fi
fi

echo
echo "=== routes ==="
ip -4 route show default 2>/dev/null | sed 's/^/  /' || true
arm_subnet="$(vj network.arm_subnet)"
for line in $(ip -br -4 addr show 2>/dev/null | grep -v '^lo' | awk '{print $1"="$3}'); do
  ifc="${line%%=*}"; a="${line#*=}"
  [[ "${a%.*}" == "${arm_subnet%.*}" && "$ifc" != "${ARM_NIC:-__}" ]] \
    && { echo "  *** $ifc has $a on the arm subnet $arm_subnet - CLASH ***"; needs+=("fix venue subnet clash on $ifc"); }
done

echo
echo "=== arm ==="
arm_ip="${1:-$(vj network.arm_default_controller_ip)}"
if ip -4 addr show 2>/dev/null | grep -q "$(vj network.arm_host_ip_cidr | cut -d/ -f1)"; then
  echo "  host has the arm-link address"
  if ping -c1 -W2 "$arm_ip" >/dev/null 2>&1; then echo "  $arm_ip answers"
  else echo "  $arm_ip does not answer - run find-arm-ip.sh (controller addresses differ per arm)"; fi
else
  echo "  arm link not configured yet"
fi

echo
echo "=== RealSense ==="
if command -v lsusb >/dev/null 2>&1; then
  lsusb 2>/dev/null | grep -i "realsense\|8086:0b" | sed 's/^/  /' || echo "  none on USB (plug it in; the module needs it at startup)"
else
  echo "  lsusb missing (usbutils); cannot check from here. The module logs the camera serial at startup."
fi

echo
echo "=== viam ==="
if [[ -f /etc/viam.json ]]; then
  python3 - <<'PY' 2>/dev/null || echo "  /etc/viam.json present but unreadable or malformed"
import json
c = json.load(open("/etc/viam.json"))["cloud"]
addr = c.get("app_address", "")
print(f"  /etc/viam.json: part id {c.get('id')}")
print(f"  app_address  : {addr}" + ("" if addr.startswith("https://app.viam.com") else "   <-- NOT PRODUCTION"))
PY
else
  echo "  no /etc/viam.json"
fi
if command -v viam-agent >/dev/null 2>&1 || [[ -x /opt/viam/bin/viam-agent ]]; then
  echo "  viam-agent: installed, $(systemctl is-active viam-agent 2>&1)"
else
  echo "  viam-agent: NOT installed"
fi
echo "  (whether that part id belongs to this event's location is checked from the laptop by provision-viam.py)"
needs+=("provision-viam.py on the laptop, then install-config.sh here (always: every box gets a fresh machine in the event location)")

echo
echo "=== NEEDS ==="
for n in "${needs[@]}"; do echo "  - $n"; done
