# Hackathon work cells (off-site)

Provisioning for hackathons away from Viam HQ: a compute box, an xArm with a wrist
RealSense and uFactory gripper, a table and a ceiling, registered in Viam under
**hackathons > \<event location\>**. Same cell as the HQ arm farm, minus the walls.

Separate from the HQ flow on purpose: everything here is under `hackathon/`, and the HQ
scripts and `config/fleet.json` are never modified by it. Shared, read-only:
`scripts/lib/pick-nics.sh` (which port is the arm), `scripts/identify-config.py` (the
mounting test) and the arm fragment ids in `config/fleet.json`.

## Use it with Claude Code

On your laptop:

```
git clone https://github.com/viamrobotics/armfarm-setup && cd armfarm-setup
claude
```

Then `/hackathon-setup`. It drives each box over ssh and hands you the few commands that
need a password (the box's sudo, the wifi).

## Use it by hand

Once per operator, on the laptop:

```
bash hackathon/scripts/bootstrap-laptop.sh            # viam CLI, venv, login, org key
```

Once per event (writes ids into the venue file; commit it):

```
hackathon/venv/bin/python hackathon/scripts/create-venue.py --venue oct-sf-hackathon --apply
```

Per box. Steps marked **box** run on the box; the first one needs a monitor and keyboard.

```
# box, at the monitor, with the switch cable plugged in:
sudo bash /home/viam/armfarm-setup/hackathon/scripts/console-bootstrap.sh sfhack3

# laptop:
ssh-copy-id viam@sfhack3.local
rsync -a --exclude venv --exclude out --exclude .git ./ viam@sfhack3.local:~/armfarm-setup/
ssh viam@sfhack3.local bash ~/armfarm-setup/hackathon/scripts/assess.sh        # read-only
ssh viam@sfhack3.local bash ~/armfarm-setup/hackathon/scripts/find-arm-ip.sh   # controller address

hackathon/venv/bin/python hackathon/scripts/provision-viam.py --venue oct-sf-hackathon \
    --name sfhack3 --arm xarm6-gripper2 --arm-ip 192.168.1.233 --apply

scp hackathon/out/viam.json.sfhack3 viam@sfhack3.local:~/
ssh -t viam@sfhack3.local sudo bash ~/armfarm-setup/hackathon/scripts/install-config.sh ~/viam.json.sfhack3

hackathon/venv/bin/python hackathon/scripts/verify-cell.py --venue oct-sf-hackathon --name sfhack3 --image
```

## Layout

| path | purpose |
|---|---|
| [`venues/<slug>.json`](venues/) | per-event ids and defaults (org, location, obstacles fragment, machine prefix, network, table/ceiling) |
| [`fragments/hackathon-workcell-table-ceiling.json`](fragments/hackathon-workcell-table-ceiling.json) | source of truth for the event obstacles fragment |
| [`scripts/bootstrap-laptop.sh`](scripts/bootstrap-laptop.sh) | laptop: CLI, venv, login, org key |
| [`scripts/create-venue.py`](scripts/create-venue.py) | laptop, once per event: location + obstacles fragment |
| [`scripts/console-bootstrap.sh`](scripts/console-bootstrap.sh) | box, root, at the monitor: packages, hostname, ssh, no-sleep, arm + LAN network (wifi only with `--wifi`) |
| [`scripts/no-sleep.sh`](scripts/no-sleep.sh) | box, root: mask suspend, logind ignores lid/idle, GNOME auto-suspend off (called by console-bootstrap; standalone for boxes done earlier) |
| [`scripts/setup-network.sh`](scripts/setup-network.sh) | box, root: arm link + venue LAN profiles (called by console-bootstrap; standalone for re-runs) |
| [`scripts/assess.sh`](scripts/assess.sh) | box, read-only: what does this box still need |
| [`scripts/find-arm-ip.sh`](scripts/find-arm-ip.sh) | box, no root: sweep the arm subnet for the controller |
| [`scripts/provision-viam.py`](scripts/provision-viam.py) | laptop: machine in the event location, fragments, key, `out/viam.json.<name>` |
| [`scripts/install-config.sh`](scripts/install-config.sh) | box, root: install credentials, (install viam-agent on a fresh box), restart |
| [`scripts/verify-cell.py`](scripts/verify-cell.py) | laptop: resource states, camera frame, mounting question |
| `out/` | gitignored: per-machine credentials and camera frames |

## Conventions that differ from HQ

- **Every box gets a fresh machine** in the event location, borrowed arm farm boxes
  included. Their HQ machine is left alone; `install-config.sh` backs up the old
  `/etc/viam.json` so the box can go back to HQ by restoring it.
- **No walls.** The event obstacles fragment is table + ceiling. The ceiling is there to
  stop the planner swinging the arm over the top. Obstacles constrain `motion.Move`
  only; jogging and `MoveToJointPositions` ignore them.
- **No camera serial.** Empty `serial_number` makes the RealSense module take the first
  camera it finds (module README). One camera per box, so no pyrealsense2 on the box.
- **Wired-only.** The venue LAN (switch) is DHCP on the second port and the single
  default route. Wifi is not configured; `--wifi <SSID>` exists for a box that genuinely
  needs a failover route (metric 600), and `assess.sh` flags a box that is on wifi. No
  interface may be on 192.168.1.x; `console-bootstrap.sh`, `setup-network.sh` and
  `assess.sh` all shout if one is.
- **Boxes never sleep.** `no-sleep.sh` masks the systemd suspend targets, makes logind
  ignore lid, idle and sleep keys, and turns GNOME automatic suspend off. A box that
  suspends mid-event drops its arm and looks like a network fault.
- **Network profiles** are `xArm`, `venue-lan`, `venue-wifi`. The HQ `office-lan` profile
  on a borrowed box is deleted.

Everything in the root `CLAUDE.md` about mounting, orientation vectors, the gripper being
useless for telling mountings apart, and per-arm controller addresses applies unchanged.
