#!/usr/bin/env bash
# Tooling and credentials on the OPERATOR'S LAPTOP (mac or linux). Run this first.
#
#   bash hackathon/scripts/bootstrap-laptop.sh
#
# Needs no root. Idempotent. Installs nothing on any box.
#   - viam CLI (tells you how if missing)
#   - hackathon/venv with the python SDK
#   - checks you are logged in and can see the hackathons org
#   - mints an org API key into orgkey.txt at the repo root (gitignored)
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
hack="$root/hackathon"
cd "$root"

ok()   { echo "  ok    $*"; }
info() { echo "  ..    $*"; }
warn() { echo "  WARN  $*"; }

echo "=============== hackathon laptop bootstrap ==============="

# ---------------------------------------------------------------- viam CLI
if command -v viam >/dev/null 2>&1; then
  ok "viam CLI $(viam version 2>/dev/null | grep -v 'CLI Update' | head -1)"
else
  case "$(uname -s)" in
    Darwin)
      warn "viam CLI not installed. Install it, then re-run:"
      echo "        brew tap viamrobotics/brews && brew install viam"
      ;;
    Linux)
      info "installing viam CLI -> ~/.local/bin/viam"
      mkdir -p "$HOME/.local/bin"
      arch="$(uname -m)"
      case "$arch" in
        x86_64)  cli="viam-cli-stable-linux-amd64" ;;
        aarch64) cli="viam-cli-stable-linux-arm64" ;;
        *) echo "unsupported arch: $arch" >&2; exit 1 ;;
      esac
      curl -fsSL -o "$HOME/.local/bin/viam" "https://storage.googleapis.com/packages.viam.com/apps/viam-cli/$cli"
      chmod +x "$HOME/.local/bin/viam"
      ok "viam CLI installed (make sure ~/.local/bin is on PATH)"
      ;;
  esac
  command -v viam >/dev/null 2>&1 || exit 1
fi

# ------------------------------------------------------------- python SDK
if [[ -x "$hack/venv/bin/python" ]] && "$hack/venv/bin/python" -c "import viam" 2>/dev/null; then
  ok "python SDK ready (hackathon/venv)"
else
  info "creating hackathon/venv and installing the Viam SDK"
  python3 -m venv "$hack/venv"
  "$hack/venv/bin/pip" install -q --upgrade pip
  "$hack/venv/bin/pip" install -q -r "$hack/requirements.txt"
  ok "python SDK installed"
fi

# ----------------------------------------------------------------- login
if viam whoami >/dev/null 2>&1; then
  ok "logged in as $(viam whoami 2>/dev/null | grep -v 'CLI Update' | head -1)"
else
  echo
  echo "  Not logged in (or the token expired). Run this yourself - it opens a browser -"
  echo "  then re-run bootstrap:"
  echo
  echo "      viam login"
  echo
  exit 1
fi

# --------------------------------------------------------------- org key
venue_file="$(ls "$hack"/venues/*.json | head -1)"
org_id="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["org"]["id"])' "$venue_file")"
org_name="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["org"]["name"])' "$venue_file")"

if ! viam organizations list 2>/dev/null | grep -q "$org_id"; then
  warn "your account cannot see the '$org_name' org."
  echo "        Ask Shannon to add you to the hackathons org, or log in with the account that has it:"
  echo "        viam logout && viam login"
  exit 1
fi
ok "org access: $org_name"

if [[ -s orgkey.txt ]] && grep -q VIAM_API_KEY_ID orgkey.txt; then
  ok "orgkey.txt present"
else
  info "minting an org API key -> orgkey.txt"
  out="$(viam organizations api-key create --org-id "$org_id" --name "$(whoami)-$(hostname -s)-hackathon-setup" 2>&1)" \
    || { echo "$out" >&2; exit 1; }
  kid="$(printf '%s' "$out" | grep -i 'key id'    | head -1 | sed -E 's/.*[Kk]ey [Ii][Dd]:[[:space:]]*//'    | tr -d '[:space:]')"
  val="$(printf '%s' "$out" | grep -i 'key value' | head -1 | sed -E 's/.*[Kk]ey [Vv]alue:[[:space:]]*//' | tr -d '[:space:]')"
  [[ -n "$kid" && -n "$val" ]] || { echo "could not parse the key out of:" >&2; echo "$out" >&2; exit 1; }
  ( umask 077; printf 'VIAM_API_KEY_ID=%s\nVIAM_API_KEY=%s\n' "$kid" "$val" > orgkey.txt )
  ok "orgkey.txt written (gitignored, mode 600)"
fi

echo
echo "=============== ready ==============="
echo "Next: /hackathon-setup in Claude Code, or by hand:"
echo "  $hack/venv/bin/python $hack/scripts/create-venue.py --venue <slug>      # once per event"
echo "  then per box, see $hack/README.md"
