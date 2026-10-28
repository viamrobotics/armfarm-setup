#!/usr/bin/env bash
# Run AT THE MONITOR on a box, as root. Everything that needs root except installing
# the Viam credentials, in one go with one password prompt:
#
#   sudo bash /home/viam/armfarm-setup/hackathon/scripts/console-bootstrap.sh sfhack3
#
#   1. packages the rest of the flow needs (curl, openssh-server, avahi, ethtool)
#   2. hostname + mDNS + ssh
#   3. networking: arm link on one port, venue LAN on the other  (setup-network.sh)
#   4. wifi - SKIPPED by default; these boxes are wired-only
#   5. prints what comes next: the name, the IPs, and the install-config command
#
# WIRED-ONLY BY DEFAULT. Pass no wifi flag and none is configured: the venue LAN is the
# single default route. --wifi <SSID> remains for a box that genuinely needs a failover
# route, and prompts for the password. Think before reaching for it - a venue AP handing
# out 192.168.1.x collides with the arm subnet, and that looks exactly like a dead arm.
#
# Options passed through to setup-network.sh when the ports cannot be told apart:
#   --arm-nic <iface> --lan-nic <iface> --force-nic
#
# Order matters: networking runs BEFORE wifi so the wired default route is what
# identifies the LAN port on a two-port box. Have the switch cable plugged in first.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
venue_file="$(ls "$here"/../venues/*.json | head -1)"

name="${1:-}"; shift || true
ssid=""; net_args=(); no_wifi=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --wifi) ssid="$2"; shift 2 ;;
    --no-wifi) no_wifi=1; shift ;;
    --arm-nic) net_args+=(--arm-nic "$2"); shift 2 ;;
    --lan-nic) net_args+=(--lan-nic "$2"); shift 2 ;;
    --force-nic) net_args+=(--force); shift ;;
    --venue) venue_file="$here/../venues/$2.json"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

die() { echo; echo "FAILED: $*" >&2; exit 1; }
step() { echo; echo "=============== $* ==============="; }

[[ -n "$name" ]] || die "usage: sudo bash console-bootstrap.sh <machine-name> [--wifi <SSID>]"
[[ "$name" =~ ^[a-z][a-z0-9-]*$ ]] || die "name must be lowercase letters, digits, hyphens: $name"
[[ $EUID -eq 0 ]] || die "run with sudo"
[[ -f "$venue_file" ]] || die "no venue file at $venue_file"
# No wifi flag at all means wired-only, which is the normal case. --no-wifi is still
# accepted so existing commands and notes keep working, but it is now the default.
: "${no_wifi:=0}"

wifi_metric="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["network"]["wifi_route_metric"])' "$venue_file")"
wifi_profile="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["network"]["wifi_profile_name"])' "$venue_file")"

# Read the wifi password now, before anything slow, so the person is not waiting
# at a prompt halfway through. Never echoed, never on a command line that lands in
# shell history (it goes to nmcli as an argument of a non-interactive script).
wifi_pw=""
if [[ -n "$ssid" ]]; then
  read -rsp "wifi password for '$ssid' (not echoed): " wifi_pw; echo
  [[ -n "$wifi_pw" ]] || die "empty wifi password"
fi

step "1/5  packages"
need=()
command -v curl >/dev/null         || need+=(curl)
command -v ethtool >/dev/null      || need+=(ethtool)
command -v avahi-daemon >/dev/null || need+=(avahi-daemon)
command -v sshd >/dev/null         || need+=(openssh-server)
command -v nmcli >/dev/null        || need+=(network-manager)
command -v python3 >/dev/null      || need+=(python3)
if [[ ${#need[@]} -eq 0 ]]; then
  echo "  all present"
else
  echo "  installing: ${need[*]}"
  if ! ip -4 route show default | grep -q default; then
    echo "  WARNING: no default route - apt will probably fail. Plug the switch cable in."
  fi
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq || echo "  apt-get update failed (continuing)"
  apt-get install -y -qq "${need[@]}" || die "apt install failed: ${need[*]}. Get the box online and re-run."
fi

step "2/5  hostname $name, mDNS, ssh"
hostnamectl set-hostname "$name"
# avahi keeps advertising the OLD name until restarted.
systemctl enable --now avahi-daemon >/dev/null 2>&1 || true
systemctl restart avahi-daemon
systemctl enable --now ssh
ss -lntp | grep -q ':22 ' && echo "  ssh listening on 22" || die "sshd not listening"
if systemctl is-active ufw >/dev/null 2>&1; then
  ufw allow OpenSSH >/dev/null 2>&1 || true
  echo "  ufw: OpenSSH allowed"
fi

step "3/5  networking (arm link + venue LAN)"
bash "$here/setup-network.sh" --venue-file "$venue_file" ${net_args[@]+"${net_args[@]}"}

step "4/5  wifi"
if [[ -n "$ssid" ]]; then
  wdev="$(nmcli -t -f DEVICE,TYPE dev 2>/dev/null | awk -F: '$2=="wifi"{print $1; exit}')"
  [[ -n "$wdev" ]] || die "no wifi device found by NetworkManager (nmcli dev)"
  nmcli radio wifi on >/dev/null 2>&1 || true
  nmcli dev wifi rescan ifname "$wdev" >/dev/null 2>&1 || true
  sleep 3
  nmcli connection delete "$wifi_profile" >/dev/null 2>&1 || true
  if nmcli dev wifi connect "$ssid" password "$wifi_pw" ifname "$wdev" name "$wifi_profile" >/dev/null; then
    nmcli connection modify "$wifi_profile" \
      ipv4.route-metric "$wifi_metric" ipv6.route-metric "$wifi_metric" \
      connection.autoconnect yes connection.autoconnect-priority 0
    nmcli connection up "$wifi_profile" >/dev/null 2>&1 || true
    echo "  joined '$ssid' on $wdev as profile '$wifi_profile' (route metric $wifi_metric)"
  else
    echo "  WARNING: could not join '$ssid'. Check the SSID and password:"
    nmcli dev wifi list ifname "$wdev" 2>/dev/null | head -8 | sed 's/^/    /'
    echo "  Continue without wifi; re-run with --wifi later, or:"
    echo "    sudo nmcli dev wifi connect '$ssid' password '<pw>' name '$wifi_profile'"
  fi
else
  echo "  skipped - wired-only (pass --wifi <SSID> only if this box needs a failover route)"
fi
unset wifi_pw

step "5/5  summary"
echo "  hostname : $name  ->  $name.local"
ip -br -4 addr show 2>/dev/null | grep -v '^lo' | sed 's/^/  /'
echo "  routes:"
ip -4 route show default | sed 's/^/    /'

# The one thing that silently breaks the arm: another interface on the arm's subnet.
arm_subnet="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["network"]["arm_subnet"])' "$venue_file")"
arm_nic="$(nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null | awk -F: '$1=="xArm"{print $2}')"
while read -r ifc _ addr _; do
  [[ -z "$ifc" || "$ifc" == lo || "$ifc" == "$arm_nic" ]] && continue
  [[ "${addr%.*}" == "${arm_subnet%.*}" ]] || continue
  echo
  echo "  *** SUBNET CLASH: $ifc has $addr, which is on the arm subnet $arm_subnet. ***"
  echo "  *** The venue LAN or wifi is handing out 192.168.1.x. Arm traffic will route  ***"
  echo "  *** the wrong way. Fix the router/AP subnet before provisioning.              ***"
done < <(ip -br -4 addr show 2>/dev/null)

echo
echo "Next:"
echo "  working ON this box (monitor/keyboard here)? the remaining steps are local -"
echo "    provision-viam.py --venue <slug> --name $name --arm <cfg> --arm-ip <ip> --apply"
echo "    sudo bash $here/install-config.sh <repo>/hackathon/out/viam.json.$name"
echo "  driving it from a laptop? get a key on first, then continue over ssh -"
echo "    ssh-copy-id viam@$name.local      # type this box's password once"
