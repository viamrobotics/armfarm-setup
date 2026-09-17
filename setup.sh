#!/usr/bin/env bash
# One command to take a box from bare to a working arm farm machine.
#
#   sudo bash setup.sh armfarm4 --wall right --arm xarm6-gripper2
#
# Asks for your password once, then does everything: hostname/mDNS/ssh,
# networking, camera serial detection, Viam registration and fragments.
#
# Prereqs it will check for and tell you about (it never silently continues):
#   - venv/          python3 -m venv venv && venv/bin/pip install -r requirements.txt
#   - orgkey.txt     VIAM_API_KEY_ID=... / VIAM_API_KEY=...  (viam login, then mint one)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here"

name="${1:-}"; shift || true
wall=""; arm=""; cam=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --wall) wall="$2"; shift 2 ;;
    --arm)  arm="$2";  shift 2 ;;
    --cam-serial) cam="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

die() { echo; echo "FAILED: $*" >&2; exit 1; }

[[ -n "$name" ]] || die "usage: sudo bash setup.sh armfarmN --wall left|right --arm xarm6-gripper2"
[[ -n "$wall" ]] || die "--wall left|right is required (which side the wall is on)"
[[ -n "$arm"  ]] || die "--arm is required: xarm6-original|xarm6-gripper2|xarm850-original|xarm850-gripper2"
[[ $EUID -eq 0 ]] || die "run with sudo: sudo bash setup.sh $name --wall $wall --arm $arm"

[[ -x venv/bin/python ]] || die "no venv. run: python3 -m venv venv && venv/bin/pip install -r requirements.txt"
[[ -f orgkey.txt ]] || die "no orgkey.txt. run 'viam login', then:
  viam organizations api-key create --org-id \$(python3 -c 'import json;print(json.load(open(\"config/fleet.json\"))[\"org\"][\"id\"])') --name ${name}-setup
  and write VIAM_API_KEY_ID=... / VIAM_API_KEY=... into orgkey.txt"

set -a; . ./orgkey.txt; set +a
[[ -n "${VIAM_API_KEY_ID:-}" && -n "${VIAM_API_KEY:-}" ]] || die "orgkey.txt is missing VIAM_API_KEY_ID / VIAM_API_KEY"

step() { echo; echo "=============== $* ==============="; }

step "1/4  hostname, mDNS, ssh"
bash scripts/setup-host.sh "$name"

step "2/4  networking"
bash scripts/setup-network.sh

step "3/4  camera serial"
if [[ -z "$cam" ]]; then
  # The DEVICE serial, straight from the driver. sysfs reports the ASIC serial,
  # which the module does not match on -> 'failed to start device'.
  cam="$(venv/bin/python -c "
import sys
try:
    import pyrealsense2 as rs
except ImportError:
    sys.exit('pyrealsense2 not installed: venv/bin/pip install pyrealsense2')
ds = list(rs.context().query_devices())
if not ds:
    sys.exit('no RealSense found. If viam-server is running it may hold the device: sudo systemctl stop viam-agent')
print(ds[0].get_info(rs.camera_info.serial_number))
" 2>&1)" || die "$cam"
  echo "  detected: $cam"
else
  echo "  given: $cam"
fi

step "4/4  register and configure in Viam"
venv/bin/python scripts/provision-viam.py \
  --name "$name" --cam-serial "$cam" --wall "$wall" --arm "$arm" \
  --apply --write-config

echo
echo "=============== done ==============="
echo "machine : $name"
echo "arm     : $arm"
echo "camera  : $cam"
echo "wall    : $wall"
echo
echo "Check it: https://app.viam.com  -> hackathons -> Fine Motor Skills -> $name"
echo "Mounting ($arm) is still a guess until verified against the real arm."
echo "Once the arm is up, get GetEndPosition and run:"
echo "  venv/bin/python scripts/identify-config.py --pose <x> <y> <z> <ox> <oy> <oz> <th>"
