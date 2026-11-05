# Fleet runbook: clear the cells, then check every camera mounting

For the person (or session) handed a room full of cells the morning of an event. It
assumes the cells are already built per [`hackathon/README.md`](../README.md); this is
what to do with all of them at once.

Two scripts:

| | |
|---|---|
| [`clear-to-fragment.py`](../scripts/clear-to-fragment.py) | put a machine back to arm + camera + gripper + table + ceiling + walls |
| [`check-depth-alignment.py`](../scripts/check-depth-alignment.py) | move every arm and decide PASS/FAIL on the camera mounting |

## 0. Before anything

```
bash hackathon/scripts/bootstrap-laptop.sh      # venv, login, orgkey.txt
```

`clear-to-fragment.py` needs `arm_fragments` in the venue file. It is per-event: a new
event makes new fragments, so fill in the ids before the first run.

## 1. Clear the cells

Dry run first. It prints, per machine, what it would drop and what it promotes.

```
hackathon/venv/bin/python hackathon/scripts/clear-to-fragment.py --venue <slug> --all
```

**Read the output before applying.** Two lines matter:

* `PROMOTED arm-ip ... -> ...` / `PROMOTED cam-sn ... -> ...`
  A fragment_mod held the real value while the fragment variable held a placeholder. The
  mod wins, so the script copies it into the variable before dropping mods. Seen in the
  field: a machine whose `arm-ip-address` variable was the **gateway** address, with the
  real controller address only in a mod.

* `REFUSED: unrecognised component(s) [...]`
  The machine carries a component that is not an obstacle and not a wall. It may be
  **project work** (safe to drop) or a **hardware fact** (not safe). A cell with a vacuum
  end effector is described by a `vacuum` component *plus* a
  `components.gripper.disabled` mod; delete those and the fragment's two-finger gripper
  returns on an arm that has not got one, and the controller reports
  `End Module Communication Error`. Inspect, then `--keep <name>` or `--force`.

Then:

```
hackathon/venv/bin/python hackathon/scripts/clear-to-fragment.py --venue <slug> --all --apply --restart
```

**Restart the parts.** Removing components does not reliably rebuild the motion service's
frame system. The cell looks perfectly healthy - resources READY, frames clean, config
correct - and refuses every planned move with
`Some frames are not linked to the world frame. Unlinked parts: [...]`.
`--restart` prints the commands; run them.

## 2. Check the mountings

```
# dry run: connects, reports resource health, moves nothing
hackathon/venv/bin/python hackathon/scripts/check-depth-alignment.py --venue <slug>

# prove it on one arm of EACH MODEL first
hackathon/venv/bin/python hackathon/scripts/check-depth-alignment.py --venue <slug> \
    --only <an-xarm6>,<an-xarm850> --move

# then the fleet - every arm moves at once
hackathon/venv/bin/python hackathon/scripts/check-depth-alignment.py --venue <slug> --move
```

Each cell gets PASS / FAIL / ERROR. Raw numbers land in `hackathon/out/depth-<name>.json`
- keep them; they are the only evidence if a verdict looks wrong later.

### Reading the result

`cfg` is the tilt of the table under the configured camera frame; `flip` is the tilt under
the other mounting. A good cell is **flat one way and steeply wrong the other**:

```
PASS   sfhack9   tilt-fwd:cfg0/flip50 tilt-rgt:cfg0/flip50
```

Across 16 cells at the Oct SF Hackathon, `cfg` was **0-2 deg** and `flip` **48-51 deg** -
decided by a factor of ~25. Anything near the 5 deg / 20 deg thresholds deserves a look.

* `FAIL  mounting is the OTHER candidate` - swap the fragment, then re-run.
* `FAIL  neither hypothesis gives a flat table` - arm model, table height, or calibration.
* `ERROR not conclusive` - the tool was not tilted enough; a pose problem, not a cell problem.

## 3. Failure signatures

Most of what stops a fleet check is not configuration.

| what you see | what it means | who fixes it |
|---|---|---|
| `Emergency Stop Button Pushed In` | the physical button | someone at the cell |
| `End Module Communication Error` | controller lost the tool-side board. **Or** a standard gripper is configured on a cell with a vacuum one | check the wrist cable; check the config describes the real end effector |
| `Number of connected devices: 0` | no RealSense enumerating - absent, unplugged, or a USB port that did not come back | reseat the camera |
| `DeadlineExceeded` dialling the arm | controller unreachable: powered off, or the arm cable is out | power and cable at the controller |
| `connect failed: ConnectionError` | the **box** is offline, not the arm | power/network/viam-agent on the box |
| `Unlinked parts: [...]` | stale frame system after a config change | `viam machines part restart --part <id>` |
| `point cloud timed out` | bandwidth, with many cells capturing at once | raise `--capture-timeout`, raise `--thin` |

E-stops were the single biggest blocker in practice - three cells, all healthy the moment
the button was released. Check those before suspecting the config.

## 4. Testing changes without hardware

A simulated cell is enough to exercise motion and obstacle avoidance:

```json
{ "name": "arm", "api": "rdk:component:arm", "model": "rdk:builtin:fake",
  "attributes": { "arm-model": "xarm6" },
  "frame": { "parent": "world", "translation": { "x": 0, "y": 0, "z": 0 },
             "orientation": { "type": "ov_degrees", "value": { "x":0,"y":0,"z":1,"th":0 } } } }
```

**`arm-model` is lowercase**: `xarm6`, `lite6`, `ur5e` are accepted; `xArm6` and `xArm7`
are rejected with `unsupported arm-model`. The fake `xarm6` is faithful to the real cells
- all joints zero puts the end effector at `(207, 0, 112)`, exactly where the hardware
lands. Add the `table` and `wall-back` obstacles and the planner refuses a pose behind the
wall, so obstacle handling can be exercised too.
