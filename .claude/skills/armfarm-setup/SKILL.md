---
name: armfarm-setup
description: Provision a new arm farm machine (armfarmN) end to end - hostname, ssh, networking, and Viam registration with hardware and obstacle fragments. Use when setting up, re-imaging, or troubleshooting an arm farm compute box, or when the user mentions armfarm, the arm farm, or adding an arm to Fine Motor Skills.
---

# Provision an arm farm machine

Read `config/fleet.json` for all ids and defaults. Read `CLAUDE.md` for conventions and the
list of gotchas — several of them look like unrelated problems when you hit them cold.

Work **verify-then-apply**: gather facts, show the user what you intend to do, apply on
confirmation. Every script dry-runs by default.

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

You need the **device** serial, not the ASIC serial. `rs-enumerate-devices` if installed;
otherwise apply the config with any value, then read the real one from the module's startup
log — it prints both, and the error names the one that failed.

## 5. Register and configure in Viam

```
./scripts/provision-viam.py --name armfarmN --cam-serial <DEVICE serial> --wall left|right
./scripts/provision-viam.py --name armfarmN --cam-serial <serial> --wall left --apply --write-config
```

Needs an org-scoped key in `VIAM_API_KEY_ID` / `VIAM_API_KEY`. Have the user run
`viam login` as themselves and mint one; don't reuse someone else's.

Then `sudo systemctl restart viam-agent`.

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
