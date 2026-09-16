# Geometry conventions

## Frame

Arm base at the world origin.

| axis | direction |
|---|---|
| `+X` | forward — the way the arm reaches at J1 = 0 |
| `+Y` | to the arm's left |
| `+Z` | up |

Confirm `+X` physically before trusting anything below. `GetEndPosition` on an extended
arm tells you which way it reaches; only your eyes tell you whether that is the front.

## Obstacles: centers, not faces

`config/fleet.json` stores box **centers**. The number you measure is a **face**. They are
not the same and mixing them up puts a wall half its thickness out of place.

```
face = center ± size/2
```

Current fleet (walls 100 thick, table 200, ceiling 100):

| obstacle | center | size | face |
|---|---|---|---|
| table | `(0, 0, -123)` | `3000 × 3000 × 200` | top `z = -23` |
| front wall | `(740, 0, 300)` | `100 × 3000 × 1600` | inner `x = 690` |
| side wall | `(0, ±500, 300)` | `3000 × 100 × 1600` | inner `|y| = 450` |
| ceiling | `(0, 0, 1050)` | `3000 × 3000 × 100` | underside `z = 1000` |

Two deliberate choices:

- **Everything is oversized in-plane (3000mm).** Only the perpendicular distance matters —
  the arm cannot reach the far end of a wall either way. This cuts the measuring per station
  down to four numbers.
- **Walls span `z -500 … 1100`, independent of table height.** A station with a different
  table changes only `table-translation`; wall geometry is fixed fleet-wide. Walls overlapping
  the table below its surface is harmless.

## Left vs right is a value, not a fork

The side wall box is identical either way. Only its Y sign changes, and that is supplied per
machine at import:

```
left   {x: 0, y:  500, z: 300}
right  {x: 0, y: -500, z: 300}
```

One fragment for the fleet. Forking it would create two things to keep in step — which is
exactly how the upstream pair `ufactory-xarm6-realsense` /
`ufactory-xarm-realsense-not-backwards` ended up disagreeing, with a bug in one of them.

## Orientation vectors and 180° flips

Viam's orientation vector is `R = Rz(lon)·Ry(lat)·Rz(th)`, where the axis `(x,y,z)` encodes
`lon`/`lat` and `th` is the spin about that axis.

Rotating a frame 180° about its **parent's** Z means left-multiplying by `Rz(180)`, which
lands in `lon` — i.e. **the axis's X and Y flip sign and `th` is unchanged**.

The exception is an axis exactly at the pole `(0,0,1)`: there is no `lon` to carry the
rotation, so it goes into `th` instead.

Both appear in the robot fragment:

| component | axis | effect of the 180° flange rotation |
|---|---|---|
| `cam` | `(0.030391, 0.003538, 0.999532)` — near the pole, not on it | axis X/Y flipped, `th` stays `-97.731173` |
| `gripper` | `(0, 0, 1)` — exactly the pole | axis unchanged, `th` becomes `180` |

Getting this wrong is easy and the symptoms are misleading — a wrong `th` looks like a
calibration error rather than an encoding error. If a frame looks wrong, **do not tune the
numbers**. Compute where each candidate puts the part in world coordinates and have someone
look at the robot. Position is 168mm apart between the two hypotheses and decides it in
seconds; orientation by eye does not.

## Do not add collision geometry for cam or gripper

Both drivers already publish it, and more accurately than a hand-written box:

```
gripper   box "case-gripper"  75 × 110 × 110   center z -55
          box "claws"         45 × 120 × 112   center z -6
cam       box "box"           90 × 25 × 25     center (32.2, 0, -8.3)
```

A configured `frame.geometry` **overrides** the model's, so adding one replaces a two-box
gripper model and a correctly-offset camera box with a guess. Check with `GetGeometries`
before assuming anything is unmodelled.

The camera **bracket** is genuinely unmodelled by both drivers.
