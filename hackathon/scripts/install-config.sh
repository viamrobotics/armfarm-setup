#!/usr/bin/env bash
# Put this machine's Viam credentials in place and get viam-agent running. Root; the
# one sudo step after console-bootstrap. The laptop makes the viam.json
# (provision-viam.py) and copies it over; this installs it:
#
#   sudo bash ~/armfarm-setup/hackathon/scripts/install-config.sh ~/viam.json.sfhack3
#
#   - viam-agent already installed (borrowed arm farm box): back up the old file,
#     install the new one, clear the cached cloud config, restart the agent
#   - no viam-agent (fresh box): run Viam's official installer with the credentials in
#     the environment; it writes /etc/viam.json and sets up the service itself
#
# Either way it waits for the agent to come up and prints its state.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }
src="${1:?usage: install-config.sh <viam.json produced by provision-viam.py>}"
[[ -s "$src" ]] || { echo "no such file: $src"; exit 1; }

INSTALLER_URL="https://storage.googleapis.com/packages.viam.com/apps/viam-agent/install.sh"

read -r part_id key_id key app_addr < <(python3 - "$src" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))["cloud"]
print(c["id"], c["api_key"]["id"], c["api_key"]["key"], c.get("app_address", ""))
PY
)
[[ "$app_addr" == https://app.viam.com* ]] || { echo "refusing: app_address is '$app_addr', not production"; exit 1; }
echo "== part $part_id -> $app_addr =="

agent_present=0
{ command -v viam-agent >/dev/null 2>&1 || [[ -x /opt/viam/bin/viam-agent ]] \
  || systemctl list-unit-files viam-agent.service 2>/dev/null | grep -q viam-agent; } && agent_present=1

if [[ $agent_present -eq 1 ]]; then
  echo "== viam-agent present: swapping credentials =="
  if [[ -f /etc/viam.json ]]; then
    old_part="$(python3 -c 'import json;print(json.load(open("/etc/viam.json"))["cloud"].get("id",""))' 2>/dev/null || true)"
    bak="/etc/viam.json.bak.$(date +%Y%m%d-%H%M%S)"
    cp /etc/viam.json "$bak"
    echo "   previous part ${old_part:-?} backed up to $bak"
  fi
  install -m 644 "$src" /etc/viam.json
  # Stale cached config for the OLD part would be loaded first; remove it.
  for c in /root/.viam/cached_cloud_config*.json /opt/viam/cached_cloud_config*.json; do
    [[ -e "$c" ]] && { rm -f "$c"; echo "   cleared $c"; }
  done
  systemctl restart viam-agent
else
  echo "== no viam-agent: running Viam's installer =="
  command -v curl >/dev/null || { echo "curl missing - run console-bootstrap.sh first"; exit 1; }
  tmp="$(mktemp --suffix=.sh)"
  curl -fsSL "$INSTALLER_URL" -o "$tmp"
  [[ $(stat -c %s "$tmp") -gt 500 ]] || { echo "installer download looks truncated"; exit 1; }
  # Credentials through the environment, never on a command line.
  VIAM_API_KEY_ID="$key_id" VIAM_API_KEY="$key" VIAM_PART_ID="$part_id" FORCE=1 /bin/sh "$tmp"
  rm -f "$tmp"
fi

echo "== waiting for viam-agent =="
for i in $(seq 1 30); do
  st="$(systemctl is-active viam-agent 2>&1 || true)"
  [[ "$st" == active ]] && break
  sleep 2
done
echo "   viam-agent: $st"
sleep 5
echo "== recent agent log =="
journalctl -u viam-agent --no-pager -n 12 2>/dev/null | cut -c1-160 | sed 's/^/   /' || true
echo
echo "Check from the laptop: verify-cell.py, or https://app.viam.com"
