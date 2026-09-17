#!/usr/bin/env bash
# Tooling and credentials for an arm farm box. Run this FIRST, before setup.sh.
#
#   bash scripts/bootstrap.sh
#
# Needs NO root. Installs the Viam CLI and the Python SDK, then gets you an
# org API key. Idempotent - safe to re-run; it skips whatever is already done.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"

ok()   { echo "  ok    $*"; }
info() { echo "  ..    $*"; }
warn() { echo "  WARN  $*"; }

echo "=============== arm farm bootstrap ==============="

# ---------------------------------------------------------------- viam CLI
# Not in apt. Installs per-user so nothing here needs sudo.
mkdir -p "$HOME/.local/bin"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) warn "$HOME/.local/bin is not on PATH - add it to ~/.bashrc:"
     echo "        export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

if command -v viam >/dev/null 2>&1; then
  ok "viam CLI $(viam version 2>/dev/null | head -1)"
else
  info "installing viam CLI -> ~/.local/bin/viam"
  arch="$(uname -m)"
  case "$arch" in
    x86_64)  cli="viam-cli-stable-linux-amd64" ;;
    aarch64) cli="viam-cli-stable-linux-arm64" ;;
    *) echo "unsupported arch: $arch" >&2; exit 1 ;;
  esac
  curl -fsSL -o "$HOME/.local/bin/viam" "https://storage.googleapis.com/packages.viam.com/apps/viam-cli/$cli"
  chmod +x "$HOME/.local/bin/viam"
  ok "viam CLI installed"
fi

# ------------------------------------------------------------- python SDK
# Ubuntu 24.04 is PEP 668 managed, so a system pip install fails. Use a venv.
# It must live at venv/ - that is the path .gitignore covers (.venv/ is not).
if [[ -x venv/bin/python ]] && venv/bin/python -c "import viam" 2>/dev/null; then
  ok "python SDK ready (venv/)"
else
  info "creating venv/ and installing the Viam SDK"
  python3 -m venv venv
  venv/bin/pip install -q --upgrade pip
  venv/bin/pip install -q -r requirements.txt
  ok "python SDK installed"
fi

# ------------------------------------------------------------ ~/.viam perms
# A box built by someone running `sudo viam login` has a root-owned ~/.viam,
# and `viam login` as a normal user then cannot cache its token.
if [[ -e "$HOME/.viam" && ! -w "$HOME/.viam" ]]; then
  warn "~/.viam is not writable (owned by $(stat -c '%U' "$HOME/.viam"))."
  echo "        viam login will fail until you run:"
  echo "        sudo chown -R \"\$USER:\$USER\" ~/.viam"
  exit 1
fi

# ----------------------------------------------------------------- login
if viam whoami >/dev/null 2>&1; then
  ok "logged in as $(viam whoami 2>/dev/null | head -1)"
else
  echo
  echo "  Not logged in. Run this yourself (it opens a browser), then re-run bootstrap:"
  echo
  echo "      viam login"
  echo
  exit 1
fi

# --------------------------------------------------------------- org key
org_id="$(python3 -c 'import json;print(json.load(open("config/fleet.json"))["org"]["id"])')"
org_name="$(python3 -c 'import json;print(json.load(open("config/fleet.json"))["org"]["name"])')"

if ! viam organizations list 2>/dev/null | grep -q "$org_id"; then
  warn "your account cannot see the '$org_name' org."
  echo "        Log in with the account that has access: viam logout && viam login"
  exit 1
fi
ok "org access: $org_name"

if [[ -s orgkey.txt ]] && grep -q VIAM_API_KEY_ID orgkey.txt; then
  ok "orgkey.txt present"
else
  info "minting an org API key -> orgkey.txt"
  out="$(viam organizations api-key create --org-id "$org_id" --name "$(hostname)-armfarm-setup" 2>&1)" \
    || { echo "$out" >&2; exit 1; }
  kid="$(printf '%s' "$out" | grep -i 'key id'    | head -1 | sed -E 's/.*[Kk]ey [Ii][Dd]:[[:space:]]*//'    | tr -d '[:space:]')"
  val="$(printf '%s' "$out" | grep -i 'key value' | head -1 | sed -E 's/.*[Kk]ey [Vv]alue:[[:space:]]*//' | tr -d '[:space:]')"
  [[ -n "$kid" && -n "$val" ]] || { echo "could not parse the key out of:" >&2; echo "$out" >&2; exit 1; }
  ( umask 077; printf 'VIAM_API_KEY_ID=%s\nVIAM_API_KEY=%s\n' "$kid" "$val" > orgkey.txt )
  ok "orgkey.txt written (gitignored, mode 600)"
fi

echo
echo "=============== ready ==============="
echo "Next, with the arm plugged in and powered:"
echo
echo "  sudo bash $here/setup.sh armfarmN --wall left|right --arm xarm6-gripper2"
echo
echo "  --arm is one of: xarm6-original xarm6-gripper2 xarm850-original xarm850-gripper2"
