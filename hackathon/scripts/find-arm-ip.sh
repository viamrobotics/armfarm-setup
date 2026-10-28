#!/usr/bin/env bash
# Which address is the arm controller on? Run on the box after networking is set up.
# No root needed.
#
#   bash ~/armfarm-setup/hackathon/scripts/find-arm-ip.sh
#
# uFactory ships controllers on 192.168.1.212 but they get readdressed, and every arm
# at the event is a different arm. Sweeps the arm subnet and reports every host that
# answers, marking the ones that look like an xArm controller (Modbus on tcp/502 and
# UFactory Studio on tcp/18333).
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
venue_file="$(ls "$here"/../venues/*.json | head -1)"
vj() { python3 -c 'import json,sys;d=json.load(open(sys.argv[1]))
for k in sys.argv[2].split("."): d=d[k]
print(d)' "$venue_file" "$1"; }

subnet="$(vj network.arm_subnet)"        # 192.168.1.0/24
host_ip="$(vj network.arm_host_ip_cidr)" # 192.168.1.150/24
prefix="${subnet%.*}"                    # 192.168.1
host_addr="${host_ip%/*}"

nic="$(ip -4 -o addr show | awk -v a="$host_addr/" '$4 ~ "^"a {print $2; exit}')"
if [[ -z "$nic" ]]; then
  echo "the host does not have $host_ip on any interface - run setup-network.sh first" >&2
  exit 1
fi
# The link negotiates for a second or two after the cable goes in and reads 0 in the
# gap. Seen at the Oct SF event: carrier 1, this check said 0, carrier 1 again moments
# later - which sends the operator back to a cable that was never the problem. Wait it
# out instead of failing on the first read.
carrier() { cat "/sys/class/net/$nic/carrier" 2>/dev/null; }
if [[ "$(carrier)" != "1" ]]; then
  echo "waiting up to 10s for the link on $nic to come up ..." >&2
  for _ in $(seq 1 20); do
    sleep 0.5
    [[ "$(carrier)" == "1" ]] && break
  done
fi
if [[ "$(carrier)" != "1" ]]; then
  echo "no carrier on $nic after 10s - is the arm controller cabled and powered?" >&2
  exit 1
fi

echo "sweeping $subnet on $nic (host is $host_addr) ..."
ip neigh flush dev "$nic" >/dev/null 2>&1 || true
for i in $(seq 1 254); do
  a="$prefix.$i"; [[ "$a" == "$host_addr" ]] && continue
  ping -c1 -W1 "$a" >/dev/null 2>&1 &
done
wait

port_open() { timeout 1 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; }

found=0
while read -r a _ _ _ mac state; do
  [[ "$state" == FAILED || -z "$a" ]] && continue
  found=1
  tags=()
  port_open "$a" 502   && tags+=("modbus/502")
  port_open "$a" 18333 && tags+=("ufactory-studio/18333")
  if [[ ${#tags[@]} -gt 0 ]]; then
    echo "  $a  $mac   <-- looks like an xArm controller (${tags[*]})"
  else
    echo "  $a  $mac"
  fi
done < <(ip -4 neigh show dev "$nic" | awk '{print $1, "", "", "", $3, $NF}')

if [[ $found -eq 0 ]]; then
  echo "  nothing answered. Checks:"
  echo "    - controller powered, and its ethernet cabled to $nic"
  echo "    - IPv6 link-local proves the cable even with no v4 address:"
  echo "        ping6 -c3 -I $nic ff02::1; ip -6 neigh show dev $nic"
  echo "    - the controller may be on a different subnet entirely; UFactory Studio on the"
  echo "      arm's own display shows its address"
  exit 1
fi
echo
echo "Pass the controller address to provision-viam.py as --arm-ip."
