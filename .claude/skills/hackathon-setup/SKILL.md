---
name: hackathon-setup
description: Set up an off-site hackathon work cell on a compute box - assess what the box needs, bootstrap it at the console (packages, ssh, no-sleep, arm link, venue LAN; wired-only, no wifi), register a fresh machine in the event's Viam location with the arm and table-plus-ceiling obstacle fragments, and verify the cell including the camera mounting. Use for any hackathon away from Viam HQ, when the user mentions a hackathon venue, "Oct SF Hackathon", sfhackN, or setting up cells off-site. Not for the HQ arm farm - that is /armfarm-setup.
---

# Set up a hackathon work cell

Everything for this flow is under `hackathon/`. It shares `scripts/lib/pick-nics.sh`,
`scripts/identify-config.py` and the arm fragment ids in `config/fleet.json` with the HQ
flow, read-only. **Never edit the HQ files** (`setup.sh`, `scripts/*.sh`,
`scripts/provision-viam.py`, `config/fleet.json`, the `armfarm-setup` skill) from this
skill; HQ is iterated independently.

Read `hackathon/venues/<slug>.json` for the event's ids and defaults, and the root
`CLAUDE.md` for the hardware gotchas (mounting, orientation vectors, arm addresses) -
they all still apply.

Work **verify-then-apply**: run the read-only step, show what you intend to do, apply on
confirmation. Python scripts dry-run by default and take `--apply`.

## Where you run

There are **two placements**, and they change every command you print. Work out which you
are in before printing anything - `hostname`, `SSH_CONNECTION` and `loginctl list-sessions`
settle it.

**A. On the operator's laptop.** The box is reached over ssh. The repo must be copied to it
with rsync before every box-side step (cheap, and it avoids stale scripts), and the first
steps need a monitor and keyboard at the box because ssh is not on yet.

**B. On the box itself**, at its monitor. Common on a borrowed arm farm box, where ssh and
the tooling are already present. Then there is no ssh and no rsync: every path is local,
the `scp` + `ssh -t` pairs in steps 2 and 6 collapse to a single local command, and the
"laptop-side" scripts (steps 0, 1, 5, 7) simply run there.

Either way, one fact dominates:

- **You cannot answer a password prompt.** Not sudo, not ssh. Every root step is one
  command the human runs, built so it asks once. Print it with absolute paths and let them
  run it. It needs a **real terminal**: a `!`-prefixed or otherwise non-interactive shell
  has no tty and sudo fails with *"a terminal is required to read the password"*.
- **Only two steps need root: 3 and 6.** Everything else is yours to run, including all
  the cloud work. If you are about to ask the human for a third thing, you have probably
  mis-split the work - check whether the script actually needs root, or only assumes it.
  (The one legitimate extra: `no-sleep.sh` on a box bootstrapped before it existed, see
  step 3.)

## 0. Laptop tooling - once per operator

```
bash hackathon/scripts/bootstrap-laptop.sh
```

Installs nothing on any box. Checks the viam CLI, makes `hackathon/venv`, checks the login
and the hackathons org, mints an org key into `orgkey.txt`. If it says to run `viam login`,
the human does that (it opens a browser) and re-runs bootstrap.

## 1. Event - once per event

```
hackathon/venv/bin/python hackathon/scripts/create-venue.py --venue <slug>          # dry run
hackathon/venv/bin/python hackathon/scripts/create-venue.py --venue <slug> --apply
```

Creates the Viam location and the table-plus-ceiling obstacles fragment if the venue file
has no ids for them, and writes the ids back. **Commit the venue file** afterwards so the
team gets the ids. For a new event, copy `hackathon/venues/oct-sf-hackathon.json`, change
`slug`, `event`, `location.name`, `machine_prefix`, blank both `id` fields, and run this.

An empty id in the venue file does **not** mean the thing is missing - someone may have
made it in the UI, or applied from another laptop. Both the dry run and the apply look it
up by name and say `reuse <id>` when it is already there, so read the plan rather than
assuming an empty id means "create". The dry run connects read-only to do this, so it needs
the org key too; it still writes nothing.

## 2. Per box: get the repo onto it and assess

The box user is `viam`. Before ssh works you need step 3 first; on a borrowed arm farm box
ssh is already on. Either way, once you can ssh:

```
ssh-copy-id viam@<name>.local                        # human types the box password once
rsync -a --exclude venv --exclude out --exclude .git <repo>/ viam@<name>.local:~/armfarm-setup/
ssh viam@<name>.local bash ~/armfarm-setup/hackathon/scripts/assess.sh
```

`assess.sh` is read-only and ends with a **NEEDS** list. Read it. It tells you whether
this is a bare box (missing curl, no ssh, no viam-agent), a borrowed arm farm box
(viam-agent present, `/etc/viam.json` pointing at some other part, HQ `office-lan`
profile), or a box already done. It also flags the one network fault that looks like a
dead arm: **another interface on 192.168.1.x** (venue LAN or wifi handing out the arm's
subnet). That must be fixed at the router, not on the box.

Borrowed boxes are **not** special-cased: every box gets a fresh machine in the event
location. Their old HQ machine stays untouched in Arm Farm and `install-config.sh` backs
up the old credentials file.

## 3. Per box: console bootstrap (the human, at the monitor, one sudo)

Have the **switch cable plugged in first** - on a two-port box the wired default route is
what tells the LAN port from the arm port. Then the human runs, on the box:

```
sudo bash /home/viam/armfarm-setup/hackathon/scripts/console-bootstrap.sh <name>
```

(If the repo is not on the box yet because ssh is off: a USB stick, or
`git clone https://github.com/viamrobotics/armfarm-setup` on the box - it is public.)

It installs missing packages, sets the hostname and mDNS, enables ssh, **makes sure the box
can never sleep** (`hackathon/scripts/no-sleep.sh`: masks the systemd suspend targets, tells
logind to ignore lid, idle and sleep keys, turns GNOME automatic suspend off), configures the
arm link and venue LAN (`hackathon/scripts/setup-network.sh`, which refuses to put the arm
on the port carrying the default route), and prints the IPs plus a **SUBNET CLASH** warning
if anything else is on 192.168.1.x.

A box that suspends mid-event takes its arm offline and looks like a network fault.
`assess.sh` reports whether `sleep.target` is masked. On a box bootstrapped before
`no-sleep.sh` existed, apply just that piece (one sudo, idempotent, no reboot):

```
ssh -t viam@<name>.local sudo bash ~/armfarm-setup/hackathon/scripts/no-sleep.sh
```

**These boxes are wired-only. Do not configure wifi.** No flag is needed: the venue LAN is
the single default route. `--wifi "<SSID>"` still exists for a box that genuinely needs a
failover route and will prompt for the password, but it is not the default and should not
be offered - a venue AP handing out 192.168.1.x collides with the arm subnet and looks
exactly like a dead arm. On a box that has been on wifi before, `assess.sh` flags the live
connection. Turning it off needs root (polkit refuses `nmcli radio` over ssh), so it is one
sudo command; the script refuses if wifi is the only default route:

```
ssh -t viam@<name>.local sudo bash ~/armfarm-setup/hackathon/scripts/wifi-off.sh
```

When a box needs both this and `no-sleep.sh`, chain them under one sudo so the human
types the password once:

```
ssh -t viam@<name>.local sudo bash -c 'bash ~viam/armfarm-setup/hackathon/scripts/no-sleep.sh && bash ~viam/armfarm-setup/hackathon/scripts/wifi-off.sh'
```

You can preview exactly what it will do to the network **without root**:
`bash hackathon/scripts/setup-network.sh --dry-run`. Worth doing on any box whose LAN port
you are about to touch.

If it stops because it cannot tell the ports apart, it names the flags: re-run with
`--arm-nic <iface> --lan-nic <iface>`. `assess.sh` lists the ports with MACs and carrier.

Names are `<machine_prefix><station>`: `sfhack1`, `sfhack2`, ...

## 4. Per box: the arm's address

Every arm at the event is a different arm, and controller addresses differ per arm.
`192.168.1.212` is only uFactory's factory default.

**Ask the person first.** They usually know it, and the arm's own display and UFactory
Studio both show it. One question costs seconds; the sweep pings 254 addresses. Do not
reach for the script before asking.

Only if nobody knows, sweep for it - with the arm cabled and powered:

```
bash ~/armfarm-setup/hackathon/scripts/find-arm-ip.sh        # on the box
```

It marks hosts answering on Modbus 502 / Studio 18333 as the controller. It waits up to 10s
for the link first: a freshly plugged cable reads `carrier 0` for a second or two while it
negotiates, and failing on that first read sends people back to a cable that was never the
problem. If nothing answers, the output lists what to check.

However you get the address, **confirm it answers before provisioning** - `ping` plus
tcp/502 and tcp/18333 is enough. A wrong `--arm-ip` provisions cleanly and then sits at
`STATE_UNHEALTHY` with a connection timeout, which reads as a broken arm rather than a
wrong address.

## 5. Per box: the two per-station facts, then register

Ask the person at the station:

- **Arm model**: xArm6 or xArm850. The controller is labelled; the 850 is visibly longer.
- **Mounting**: expected `gripper2` for every arm at this event, but it is **verified in
  step 7**, not assumed. Start with gripper2.

Then on the laptop:

```
hackathon/venv/bin/python hackathon/scripts/provision-viam.py --venue <slug> --name <name> \
    --arm xarm6-gripper2 --arm-ip <controller ip>                 # dry run, shows the config
... --apply
```

`--arm` is `<xarm6|xarm850>-<original|gripper2>`. Camera serial defaults to empty, which
makes the RealSense module take the one camera on the box - no serial detection needed.
`--table-z` overrides the table center if a station's table height differs (top face is
center + 100).

It creates or reuses the machine, applies the arm fragment and the venue obstacles
fragment, mints a machine key and writes `hackathon/out/viam.json.<name>`, then prints the
two commands for step 6.

## 6. Per box: install credentials (the human, one sudo)

On the box (placement B) there is nothing to copy - it is one local command:

```
sudo bash <repo>/hackathon/scripts/install-config.sh <repo>/hackathon/out/viam.json.<name>
```

From a laptop (placement A), send it over first:

```
scp hackathon/out/viam.json.<name> viam@<name>.local:~/
ssh -t viam@<name>.local sudo bash ~/armfarm-setup/hackathon/scripts/install-config.sh ~/viam.json.<name>
```

Borrowed box: backs up the old `/etc/viam.json`, installs the new one, clears the cached
cloud config, restarts viam-agent. Fresh box: runs Viam's official installer with the
credentials in the environment (it fetches and runs a remote script as root - say so).
It refuses a credentials file whose `app_address` is not production.

## 7. Per box: verify, including the mounting

```
hackathon/venv/bin/python hackathon/scripts/verify-cell.py --venue <slug> --name <name> --image
```

Through the cloud: resource states (arm, cam, gripper, table, ceiling all READY), a camera
frame saved to `hackathon/out/`, and the arm pose turned into the **camera-position
question**. Ask it exactly as printed:

> Looking at the wrist, is the camera nearer the arm's base, or further from it?

The two mountings put the camera ~168mm apart; the answer settles it. If it disagrees with
the fragment, swap with `provision-viam.py ... --arm <model>-<other mounting> --apply
--config-only`, which changes only the fragment list. **Never decide mounting from the
gripper** (it sits on the tool axis and does not move between mountings) and never tune
frame numbers by eye.

Expect the arm to be the last resource up, and expect **"Emergency Stop Button Pushed
In"**: that is the physical button on the controller, not configuration.

Also check the 3D scene in https://app.viam.com against the real cell: the camera on the
correct side of the wrist is the meaningful check. The ceiling exists only to stop the
motion planner swinging the arm over the top; there are no walls at this event.

## Scope and iteration

Do the deterministic work with the scripts. If a script is wrong, fix it under
`hackathon/` and commit, so the next box benefits. Shared files (`scripts/lib/pick-nics.sh`,
`scripts/identify-config.py`) may be edited only in ways that keep the HQ callers working.
