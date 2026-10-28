---
name: hackathon-setup
description: Set up an off-site hackathon work cell on a compute box - assess what the box needs, bootstrap it at the console (packages, ssh, arm link, venue LAN, wifi failover), register a fresh machine in the event's Viam location with the arm and table-plus-ceiling obstacle fragments, and verify the cell including the camera mounting. Use for any hackathon away from Viam HQ, when the user mentions a hackathon venue, "Oct SF Hackathon", sfhackN, or setting up cells off-site. Not for the HQ arm farm - that is /armfarm-setup.
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

**You run on the operator's laptop.** The box is reached over ssh. Three facts shape
everything below:

- **You cannot answer a password prompt.** Not sudo, not ssh, not wifi. Every root step on
  a box is one command the human runs, built so it asks once. Print the command with
  absolute paths and let them run it; the human is often in another terminal.
- The first steps on a box need **a monitor and keyboard at the box** (ssh is not on yet).
  The human runs `console-bootstrap.sh` there. After that everything is over ssh.
- The repo must be **on the box** for the box-side scripts. Copy it with rsync (below)
  before every box-side step; it is cheap and avoids stale scripts.

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
sudo bash /home/viam/armfarm-setup/hackathon/scripts/console-bootstrap.sh <name> --wifi "<SSID>"
```

(If the repo is not on the box yet because ssh is off: a USB stick, or
`git clone https://github.com/viamrobotics/armfarm-setup` on the box - it is public.)

It installs missing packages, sets the hostname and mDNS, enables ssh, configures the arm
link and venue LAN (`hackathon/scripts/setup-network.sh`, which refuses to put the arm on
the port carrying the default route), joins wifi as a higher-metric failover route, and
prints the IPs plus a **SUBNET CLASH** warning if anything else is on 192.168.1.x.
It prompts for the wifi password; the human types it. Captive-portal wifi will not work on
a headless box - use a network with a plain password.

If it stops because it cannot tell the ports apart, it names the flags: re-run with
`--arm-nic <iface> --lan-nic <iface>`. `assess.sh` lists the ports with MACs and carrier.

Names are `<machine_prefix><station>`: `sfhack1`, `sfhack2`, ...

## 4. Per box: the arm's address

Every arm at the event is a different arm, and controller addresses differ per arm.
`192.168.1.212` is only uFactory's factory default. With the arm cabled and powered:

```
ssh viam@<name>.local bash ~/armfarm-setup/hackathon/scripts/find-arm-ip.sh
```

It sweeps the arm subnet and marks hosts answering on Modbus 502 / Studio 18333 as the
controller. If nothing answers, the output lists what to check. Also ask: the arm's own
display and UFactory Studio show the address.

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

## 6. Per box: install credentials (the human, one sudo over ssh)

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
