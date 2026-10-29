#!/usr/bin/env bash
# Make sure this box can never suspend, hibernate, or sleep on idle. Root.
# Called by console-bootstrap.sh; also safe to run on its own on a box that was
# bootstrapped before this existed:
#
#   ssh -t viam@<name>.local sudo bash ~/armfarm-setup/hackathon/scripts/no-sleep.sh
#
# Three layers, because any one of them alone has a hole:
#   1. mask the systemd sleep targets      - nothing can suspend, whoever asks
#   2. logind: ignore lid, idle, sleep keys - so logind never asks
#   3. GNOME power settings (if present)   - so the desktop never asks either, and
#                                            the Settings UI shows "Never"
# Idempotent. Takes effect immediately; no reboot.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }

echo "== 1. mask systemd sleep targets =="
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null 2>&1 || true
for t in sleep suspend hibernate hybrid-sleep; do
  printf "   %-14s %s\n" "$t.target" "$(systemctl is-enabled $t.target 2>&1 || true)"
done

echo "== 2. logind: ignore lid, idle, and sleep keys =="
mkdir -p /etc/systemd/logind.conf.d
cat > /etc/systemd/logind.conf.d/90-hackathon-no-sleep.conf <<'EOF'
# Written by armfarm-setup/hackathon/scripts/no-sleep.sh. Hackathon boxes must stay up.
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
HandleSuspendKey=ignore
HandleHibernateKey=ignore
IdleAction=ignore
EOF
# Reload, not restart: restarting logind strips a running Wayland desktop of its 
# input/display devices ("NotInControl"), freezing the console with no way back short 
# of a power cycle.
systemctl reload systemd-logind 2>/dev/null || echo "   (logind restart failed; drop-in applies at next boot)"
echo "   /etc/systemd/logind.conf.d/90-hackathon-no-sleep.conf"

echo "== 3. GNOME power settings =="
if command -v dconf >/dev/null 2>&1; then
  mkdir -p /etc/dconf/profile /etc/dconf/db/local.d
  # A system profile so the local db is consulted for every user.
  if [[ ! -f /etc/dconf/profile/user ]]; then
    printf 'user-db:user\nsystem-db:local\n' > /etc/dconf/profile/user
  elif ! grep -q '^system-db:local' /etc/dconf/profile/user; then
    echo 'system-db:local' >> /etc/dconf/profile/user
  fi
  cat > /etc/dconf/db/local.d/90-hackathon-no-sleep <<'EOF'
# Written by armfarm-setup/hackathon/scripts/no-sleep.sh
[org/gnome/settings-daemon/plugins/power]
sleep-inactive-ac-type='nothing'
sleep-inactive-battery-type='nothing'
sleep-inactive-ac-timeout=0
sleep-inactive-battery-timeout=0
idle-dim=false
[org/gnome/desktop/session]
idle-delay=uint32 0
EOF
  dconf update
  echo "   system-wide: automatic suspend off, screen never blanks"
  # Also set it for the logged-in desktop user directly, in case their user db
  # already carries an explicit value that would override the system default.
  for u in $(loginctl list-users --no-legend 2>/dev/null | awk '{print $2}'); do
    uid="$(id -u "$u" 2>/dev/null || true)"; [[ -n "$uid" ]] || continue
    bus="unix:path=/run/user/$uid/bus"; [[ -S "/run/user/$uid/bus" ]] || continue
    sudo -u "$u" DBUS_SESSION_BUS_ADDRESS="$bus" gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing' 2>/dev/null || true
    sudo -u "$u" DBUS_SESSION_BUS_ADDRESS="$bus" gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type 'nothing' 2>/dev/null || true
    sudo -u "$u" DBUS_SESSION_BUS_ADDRESS="$bus" gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null || true
    echo "   user $u: automatic suspend off"
  done
else
  echo "   no dconf (no GNOME) - skipped; layers 1 and 2 cover it"
fi

echo "== result =="
if [[ "$(systemctl is-enabled sleep.target 2>&1)" == "masked" ]]; then
  echo "   sleep is masked. This box will not suspend."
else
  echo "   WARNING: sleep.target is not masked: $(systemctl is-enabled sleep.target 2>&1)"
  exit 1
fi
