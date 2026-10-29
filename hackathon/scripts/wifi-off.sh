#!/usr/bin/env bash
# Take a box off wifi and keep it off. Root. Hackathon boxes are wired-only: the venue
# LAN is the single default route, and a venue AP handing out 192.168.1.x collides with
# the arm subnet in a way that looks like a dead arm.
#
#   ssh -t viam@<name>.local sudo bash ~/armfarm-setup/hackathon/scripts/wifi-off.sh
#
# Idempotent. The radio state persists across reboots (NetworkManager stores it), and
# every wifi profile is set to not autoconnect, so a later "radio on" does not quietly
# rejoin. Reversible: nmcli radio wifi on; nmcli connection up <profile>.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }

# Refuse to cut the only route. If the wired LAN has no default route, turning the
# radio off strands the box.
wired_default="$(ip -4 route show default 2>/dev/null | awk '$5 !~ /^wl/ {print $5; exit}')"
if [[ -z "$wired_default" ]]; then
  echo "refusing: no wired default route. Only wifi is carrying traffic; fix the LAN first." >&2
  ip -4 route show default | sed 's/^/  /' >&2
  exit 1
fi
echo "== wired default route on $wired_default; safe to drop wifi =="

while IFS=: read -r pname ctype; do
  [[ "$ctype" == "802-11-wireless" ]] || continue
  nmcli connection modify "$pname" connection.autoconnect no >/dev/null 2>&1 \
    && echo "   profile '$pname': autoconnect off" \
    || echo "   profile '$pname': could not modify (continuing)"
done < <(nmcli -t -f NAME,TYPE connection show 2>/dev/null)

nmcli radio wifi off
sleep 2
echo "== result =="
echo "   radio: $(nmcli radio wifi)"
nmcli -t -f DEVICE,TYPE,STATE dev 2>/dev/null | awk -F: '$2=="wifi"{print "   "$1": "$3}'
echo "   routes:"
ip -4 route show default | sed 's/^/     /'
[[ "$(nmcli radio wifi)" == "disabled" ]] || { echo "   WARNING: radio still enabled"; exit 1; }
