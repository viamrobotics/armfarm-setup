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

Decide from it:
- **arm model** — xArm6 or xArm850? The 850 needs its own fragment and its own hand-eye
  calibration; do not reuse the xArm6 camera offsets.
- **compute** — Meerkat or UDM 90? Should not matter: NIC selection is by capability. If
  `inspect.sh` couldn't classify a NIC, stop and work out why rather than hardcoding a name.
- **which side the wall is on** — ask the user; it isn't discoverable from the machine.

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
