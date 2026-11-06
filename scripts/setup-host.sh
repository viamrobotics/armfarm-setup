#!/usr/bin/env bash
# Hostname + mDNS + ssh.  Usage: sudo bash setup-host.sh armfarm7
set -euo pipefail
NAME="${1:?usage: setup-host.sh armfarmN}"
[[ "$NAME" =~ ^armfarm[0-9]+$ ]] || { echo "name must match armfarmN"; exit 1; }
[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }

echo "== hostname -> $NAME =="
hostnamectl set-hostname "$NAME"

# avahi keeps advertising the OLD hostname until restarted. This bit is not optional.
echo "== restarting avahi so mDNS advertises the new name =="
systemctl restart avahi-daemon
sleep 2
avahi-resolve -n "${NAME}.local" 2>&1 | sed 's/^/  /' || echo "  (mDNS not resolving yet; harmless if the LAN link is not up)"

echo "== ssh =="
# A stock Pop!_OS / Ubuntu desktop image ships NO openssh-server, so
# `systemctl enable --now ssh` fails on a unit that does not exist and set -e kills
# the whole run here - at step 1 of 4, before networking or Viam registration.
# Install it rather than assuming the image has it.
if ! systemctl list-unit-files ssh.service sshd.service 2>/dev/null | grep -q '\.service'; then
  echo "  openssh-server not installed - installing"
  export DEBIAN_FRONTEND=noninteractive
  apt-get install -y openssh-server \
    || { apt-get update && apt-get install -y openssh-server; }
fi
# Debian/Ubuntu name the unit ssh.service; be tolerant of sshd.service.
ssh_unit=ssh
systemctl list-unit-files ssh.service 2>/dev/null | grep -q '^ssh\.service' || ssh_unit=sshd
systemctl enable --now "$ssh_unit"
# --now returns before the listener is necessarily bound; don't race it.
for _ in $(seq 10); do
  ss -lntH 2>/dev/null | grep -q ':22 ' && break
  sleep 1
done
ss -lntH 2>/dev/null | grep -q ':22 ' && echo "  listening on 22" || { echo "  ERROR: sshd not listening"; exit 1; }
if systemctl is-active ufw >/dev/null 2>&1; then
  ufw allow OpenSSH >/dev/null 2>&1 || true
  echo "  ufw: OpenSSH allowed"
fi
echo "== done: $NAME =="
