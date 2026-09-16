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
systemctl enable --now ssh
ss -lntp | grep -q ':22 ' && echo "  listening on 22" || { echo "  ERROR: sshd not listening"; exit 1; }
if systemctl is-active ufw >/dev/null 2>&1; then
  ufw allow OpenSSH >/dev/null 2>&1 || true
  echo "  ufw: OpenSSH allowed"
fi
echo "== done: $NAME =="
