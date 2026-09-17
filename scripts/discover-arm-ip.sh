#!/usr/bin/env bash
# Find this machine's xArm controller address on the arm subnet.
#
#   bash scripts/discover-arm-ip.sh            # prints the address, or exits 1
#   bash scripts/discover-arm-ip.sh --verbose  # show every host considered
#
# Why this exists: the controller address DIFFERS PER MACHINE. uFactory ships
# .212 and fleet.json holds that as a default, but arms get readdressed and many
# are not on it. Passing the wrong one gives a machine that provisions cleanly
# and then sits at STATE_UNHEALTHY with a connection timeout -- which reads like
# dead hardware. See CLAUDE.md. Unattended provisioning cannot ask a human, so
# it discovers instead of assuming.
#
# How: sweep the arm subnet, then keep only hosts with TCP 502 open. That is the
# xArm controller's Modbus/control port -- the one the Viam module connects on --
# so it distinguishes the arm from anything else sharing the segment. Identifying
# by "the only thing that answers ping" would be wrong on a subnet with a switch,
# a spare box, or a second arm on it.
#
# Requires the host to already be on the arm subnet, i.e. setup-network.sh has run.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$here/lib/pick-nics.sh"

ARM_PORT=502
VERBOSE=0
[[ "${1:-}" == "--verbose" ]] && VERBOSE=1

log() { [[ $VERBOSE -eq 1 ]] && echo "  $*" >&2 || true; }

host_cidr="$(python3 -c 'import json,pathlib;print(json.loads((pathlib.Path("'"$here"'").parent/"config"/"fleet.json").read_text())["arm"]["host_ip_cidr"])')"
host_ip="${host_cidr%%/*}"
subnet_prefix="$(echo "$host_ip" | cut -d. -f1-3)"

if ! pick_nics; then
  echo "cannot determine the arm NIC: $NIC_WHY" >&2
  exit 1
fi
log "arm NIC: $ARM_NIC"

# Without the host address on this subnet there is nothing to scan from.
if ! ip -4 addr show "$ARM_NIC" | grep -q "inet $host_ip/"; then
  echo "host is not at $host_ip on $ARM_NIC - run setup-network.sh first" >&2
  exit 1
fi

if [[ "$(cat "/sys/class/net/$ARM_NIC/carrier" 2>/dev/null || echo 0)" != "1" ]]; then
  echo "no carrier on $ARM_NIC - the arm is not cabled or not powered" >&2
  exit 1
fi

# Sweep. Backgrounded pings so 253 addresses take ~1s rather than ~4 minutes.
log "sweeping $subnet_prefix.0/24 from $ARM_NIC"
ip neigh flush dev "$ARM_NIC" >/dev/null 2>&1 || true
for i in $(seq 2 254); do
  [[ "$subnet_prefix.$i" == "$host_ip" ]] && continue
  ping -c1 -W1 -I "$ARM_NIC" "$subnet_prefix.$i" >/dev/null 2>&1 &
done
wait

mapfile -t alive < <(ip -4 neigh show dev "$ARM_NIC" 2>/dev/null \
  | awk '$1 != "" && $0 !~ /FAILED|INCOMPLETE/ {print $1}' | sort -u)

if [[ ${#alive[@]} -eq 0 ]]; then
  echo "nothing answered on $subnet_prefix.0/24 via $ARM_NIC" >&2
  echo "  check the cable, that the arm is powered, and that it is on this subnet" >&2
  exit 1
fi
log "responding hosts: ${alive[*]}"

# TCP 502 is the discriminator, not liveness.
found=()
for ip_addr in "${alive[@]}"; do
  if timeout 2 bash -c "exec 3<>/dev/tcp/$ip_addr/$ARM_PORT" 2>/dev/null; then
    log "$ip_addr: port $ARM_PORT OPEN  <- xArm controller"
    found+=("$ip_addr")
  else
    log "$ip_addr: port $ARM_PORT closed"
  fi
done

case ${#found[@]} in
  0) echo "found ${#alive[@]} host(s) on the arm subnet but none with TCP $ARM_PORT open:" >&2
     printf '  %s\n' "${alive[@]}" >&2
     echo "  none of these is an xArm controller. Is the arm powered and booted?" >&2
     exit 1 ;;
  1) echo "${found[0]}" ;;
  *) echo "AMBIGUOUS: more than one xArm controller on this subnet:" >&2
     printf '  %s\n' "${found[@]}" >&2
     echo "  pass --arm-ip explicitly; do not guess." >&2
     exit 1 ;;
esac
