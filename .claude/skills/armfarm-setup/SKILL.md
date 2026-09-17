---
name: armfarm-setup
description: Provision a new arm farm machine (armfarmN) end to end - hostname, ssh, networking, and Viam registration with hardware and obstacle fragments. Use when setting up, re-imaging, or troubleshooting an arm farm compute box, or when the user mentions armfarm, the arm farm, or adding an arm to Fine Motor Skills.
---

# Provision an arm farm machine

Read `config/fleet.json` for all ids and defaults. Read `CLAUDE.md` for conventions and the
list of gotchas — several of them look like unrelated problems when you hit them cold.

Work **verify-then-apply**: gather facts, show the user what you intend to do, apply on
confirmation. Every script dry-runs by default.

## 0. Tooling and auth — DO THIS FIRST

A fresh or repurposed box has none of this, and every later step is blocked on it. Two
commands, in this order:

```
bash scripts/bootstrap.sh          # no root: viam CLI, python SDK, org API key
viam login                         # only if bootstrap says to - it opens a browser
```

`bootstrap.sh` is idempotent. It installs the Viam CLI to `~/.local/bin`, builds `venv/`
with the SDK, checks org access, and **mints the org API key itself** into `orgkey.txt`.
Never ask someone to paste a key — once they are logged in the CLI can mint one.

Traps it handles, each of which has cost real time:

- **`~/.viam` owned by root.** A box someone built with `sudo viam login` has a root-owned
  `~/.viam`, and `viam login` as a normal user then cannot cache its token. Needs
  `sudo chown -R "$USER:$USER" ~/.viam` — bootstrap detects it and says so.
- **PEP 668.** Ubuntu 24.04 refuses a system `pip install`. The venv must be at `venv/` —
  that is the path `.gitignore` covers; `.venv/` is not.
- **The CLI is not in apt** and is usually absent entirely.

**You cannot answer a sudo password prompt.** So do not hand the user root steps one at a
time — that is what `setup.sh` is for. And always give absolute paths: the user is often in
a different terminal, in a different directory.

## The fast path: one command

Once bootstrap is done and the arm is plugged in and powered:

```
sudo bash /path/to/armfarm-setup/setup.sh armfarmN --wall left|right --arm xarm6-gripper2
```

That runs steps 2-5 below in order — hostname/mDNS/ssh, networking, camera serial, Viam
registration and fragments — with a single password prompt. It is safe to re-run: the
machine is reused rather than duplicated. The individual steps below are the debugging
path, not the normal one.

Then verify (step 6). Expect the arm to be the last thing to come up, and expect
**"Emergency Stop Button Pushed In"** — that is a physical button on the xArm controller,
not a configuration problem.

## 1. Inspect

```
bash scripts/inspect.sh
```

Gives you: compute model, both NICs with their roles, whether the arm is reachable, the
RealSense, ssh state, viam-agent state. **Read the output before doing anything.**

## 1b. Work out which of the FOUR configurations this machine is

**Do this with the person at the machine.** Two of the three answers are physical facts
you cannot read off the box; your job is to do the math and tell them precisely what to
look at, not to guess.

The farm is `{xArm6, xArm850} x {original, gripper2}` mounting. `config/fleet.json`
has all four under `arm_configs`.

**Arm model** — ask. The xArm850 has visibly longer reach and the controller is labelled.
Getting this wrong means wrong kinematics, so confirm rather than infer.

**Mounting** — this is decided by **where the camera physically is**, and there is a
decisive test. Once a config is applied and the arm is up, get its pose and run:

```
# GetEndPosition on the arm, then:
./scripts/identify-config.py --pose <x> <y> <z> <ox> <oy> <oz> <theta>
```

It prints where the camera would sit under each mounting - about **168mm apart**, mostly
horizontal when the tool points down - and the exact question to ask:

> Looking at the wrist, is the camera nearer the arm's base, or further from it?

That answer settles it in seconds.

**Never diagnose this by looking at orientation.** A wrong mounting looks like a
calibration error, and tuning the numbers to fix it produces something that is wrong in a
new way. Position is unambiguous; orientation by eye is not. This exact trap cost an hour
once already.

**Chicken and egg:** you need a config applied before you can query the arm's pose. Apply
your best guess, run the test, and switch the fragment if wrong - swapping is one delete
plus one add, and nothing else in the machine config changes.

**Wall side** — ask; not discoverable from the machine.

## 2. Hostname, mDNS, ssh

```
sudo bash scripts/setup-host.sh armfarmN
```

Confirm `armfarmN.local` resolves afterwards. If the *old* name still resolves, something
besides avahi is publishing it — on a re-image, `viam-server` caches the hostname at startup
and needs a restart to stop.

## 3. Networking

```
sudo bash scripts/setup-network.sh
```

Arm link on the onboard PCIe NIC (static `192.168.1.150/24`, never-default, MAC-bound),
office LAN on the USB NIC (DHCP, route-metric 100 so wired is default and wifi fails over).

Verify: `ping 192.168.1.212` succeeds, and `ip -4 route` shows the wired default ahead of wifi.

## 4. Camera serial

You need the **device** serial, not the ASIC serial. `setup.sh` reads it for you; to get it
by hand, ask the driver directly:

```
venv/bin/python -c "import pyrealsense2 as rs; \
  print(list(rs.context().query_devices())[0].get_info(rs.camera_info.serial_number))"
```

`pyrealsense2` is in `requirements.txt`. Do not reach for `rs-enumerate-devices` — it lives
in `librealsense2-utils`, which is **not** in Ubuntu's default repos. If the query returns
no device, viam-server is holding it: `sudo systemctl stop viam-agent` first.

## 5. Register and configure in Viam

Needs an org-scoped key in `VIAM_API_KEY_ID` / `VIAM_API_KEY`. Have the person run
`viam login` as themselves and mint one; don't reuse someone else's.

Dry run first — it prints the part config and tells you whether the agent is already there:

```
./scripts/provision-viam.py --name armfarmN --cam-serial <DEVICE serial> \
    --wall left|right --arm <one of the four configs>
```

Then apply. **Use `sudo -E`** so the env vars survive:

```
# fresh box - no viam-agent yet
sudo -E ./scripts/provision-viam.py --name armfarmN --cam-serial <s> --wall left \
    --arm xarm6-gripper2 --apply --install-agent

# box that already has the agent
sudo -E ./scripts/provision-viam.py --name armfarmN --cam-serial <s> --wall left \
    --arm xarm6-gripper2 --apply --write-config
```

`--install-agent` runs Viam's official installer, which writes `/etc/viam.json` and sets
up the service itself. It fetches and executes a remote script as root — that is the
documented install path, but say so before running it. Credentials are passed through the
environment, not the command line, so they don't show up in `ps`.

Without either flag the script writes the config to the home directory and prints the
command to install it, which is the safe default if you are unsure.

## 6. Verify

- all resources `STATE_READY`, machine `STATE_RUNNING`
- grab an image from `cam` — confirms the serial and the camera end to end
- check the 3D scene: camera on the correct side of the wrist, gripper not upside down

If the camera looks wrong in the scene, **do not guess at the numbers**. Get
`GetEndPosition` from the arm, compute where each candidate frame would put the camera in
world coordinates, and have the user look at the robot and say which is right. Position is
eyeball-able and decisive; orientation is not.

## Scope

Do the deterministic work with the scripts rather than by hand — they encode fixes for
problems that are not obvious when you meet them fresh. If a script is wrong, fix the
script and commit it, so the next machine benefits.
