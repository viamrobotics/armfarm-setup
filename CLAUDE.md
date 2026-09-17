# Arm farm setup

Provisions `armfarmN` machines: an xArm on a table with a wrist-mounted RealSense,
registered in Viam under **hackathons > Fine Motor Skills**.

Constants (org/location/fragment ids, arm IP, workcell geometry) live in
`config/fleet.json`. Read it rather than hardcoding anything.

## Layout

| path | purpose |
|---|---|
| `scripts/inspect.sh` | read-only; gathers every fact needed to provision. Run first. |
| `scripts/setup-host.sh` | hostname, mDNS, ssh |
| `scripts/setup-network.sh` | arm link + office LAN, ports chosen by `lib/pick-nics.sh` |
| `scripts/lib/pick-nics.sh` | which port is the arm and which is the LAN. Sourced by both of the above |
| `scripts/provision-viam.py` | create machine, mint key, write /etc/viam.json, apply fragments |
| `scripts/update-fragments.py` | push local JSON into a Viam fragment (app API) |
| `scripts/identify-config.py` | which of the four configs is this? (collaborative) |
| `scripts/check-fragments.py` | assert the four arm fragments agree where they must |
| `fragments/*.json` | version-controlled source of truth for each fragment |
| `config/fleet.json` | ids and defaults |

## Conventions

**Coordinate frame.** Arm base at world origin, `+X` forward (the direction the arm
reaches at J1=0), `+Y` to the arm's left, `+Z` up. Confirm `+X` physically before
trusting any geometry.

**Obstacle numbers in `fleet.json` are box CENTERS, not faces.** Face = center ± half
the box size. Current fleet: table top `z=-23`, front wall `x=690`, side wall `|y|=450`,
ceiling `z=1000`.

**Values vs structure.** Anything that differs only in *value* between machines (arm IP,
camera serial, table height, which side the wall is on) is a fragment **variable** — one
fragment for the fleet. Anything that differs in *structure* (`viam:ufactory:xArm6` vs
`xArm850`) needs a **separate fragment**, because a variable cannot parameterize `model`.

**Four arm configurations:** `{xArm6, xArm850} x {original, gripper2}` mounting, all four in
`config/fleet.json` under `arm_configs`. `cam` and `gripper` are parented to the arm
**flange**, so their frames depend only on the mounting — never on the arm model. Two
fragments sharing a mounting therefore carry identical cam/gripper frames, and
`scripts/check-fragments.py` asserts exactly that. Run it after touching any arm fragment.

Which configuration a given machine is gets decided **with the person at the machine** —
`scripts/identify-config.py` does the math and tells them what to look at. See the skill.

Do not fork a fragment to express a value. Upstream has a forked pair
(`ufactory-xarm6-realsense` / `ufactory-xarm-realsense-not-backwards`) that differ only by
mounting handedness — they drifted, and one of them is wrong. That is the failure mode.

## Gotchas that have already cost time

- **avahi advertises the old hostname** until restarted. `setup-host.sh` does this; don't skip it.
- **A stale generic NetworkManager profile will claim the arm NIC and DHCP-loop forever**
  (~45s cycle, endless desktop notifications, arm unreachable). The arm profile is bound
  **by MAC** and carries `autoconnect-priority 10` so it wins the port. `setup-network.sh`
  disables every other ethernet profile on either port — *including unbound ones*, which is
  the shape the offender usually has (`Ethernet connection 1`, attached to no device until
  the moment it grabs something).
- **Two shapes of box, and the port rule differs.** Older boxes have one onboard NIC plus a
  USB ethernet adapter: arm on the onboard port, LAN on the USB one. Newer Meerkats have
  **two onboard ethernet ports and no adapter**. Bus type cannot separate two onboard ports,
  so the LAN port is identified as **the one currently carrying the default route** and the
  arm gets the other. Consequence: **the office LAN must be plugged in and up before
  `setup-network.sh` runs** on a two-port box — otherwise it cannot tell the ports apart and
  stops, asking for `--arm-nic` / `--lan-nic`.
- **Never give the arm the port that carries the default route.** The arm profile is static
  and `never-default`; applying it to the live LAN port takes the box off the network, and
  these boxes have no wifi to fall back to (tailscale rides the same underlay, so that is
  gone too). `pick-nics.sh` refuses this outright and only yields to `--force`. The old
  "first PCIe NIC wins" rule walked straight into it on a two-port box.
- **The arm controller is `192.168.1.212`.** The host must be a *different* address on that
  subnet (`192.168.1.150`). Setting the host to `.212` fails duplicate-address detection
  every time the cable actually reaches the arm.
- **sysfs `serial` for a RealSense is the ASIC serial, not the device serial** the driver
  matches on. Wrong one => `failed to start device`. Use `rs-enumerate-devices`, or read the
  device serial out of viam-server's startup log.
- **The camera and gripper drivers already publish collision geometry.** Do NOT add
  `frame.geometry` — configured geometry overrides the model's, replacing accurate vendor
  data (a two-box gripper model, and a camera box with a 32.2mm offset) with guesses.
- **Orientation vectors:** a 180° rotation about the parent Z flips the OV axis's X/Y and
  leaves `th` **unchanged** — the rotation is carried by the axis direction, not the angle.
  The exception is an axis exactly at the pole `(0,0,1)`, where there's no axis direction to
  carry it and it goes into `th` instead. Both cases appear in the robot fragment: the camera
  (off-pole, axis flipped, `th` untouched) and the gripper (on-pole, `th: 180`).
- **Obstacles constrain planned motion only.** `motion.Move` honors them;
  `arm.MoveToJointPositions`, `arm.MoveToPosition` and app jogging do not. This is a planning
  constraint, not a safety interlock.

## Credentials

Each person authenticates as themselves: `viam login`, then create a short-lived org key
when a script needs one. Do not share a long-lived organization_owner key across the team.

## Verify, then apply

The python scripts dry-run by default and take `--apply`. The two `sudo` shell scripts are
the exception — they apply when run, because they are the fast path. `setup-network.sh`
takes `--dry-run` to print its plan instead, which is worth using on any box whose LAN port
you are about to touch.

Run `inspect.sh` first and read what it says; several of the gotchas above show up there
before they cost anyone an hour.
