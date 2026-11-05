#!/usr/bin/env python3
"""Is every cell's camera mounting right? Moves all arms at once and prints PASS/FAIL.

  check-depth-alignment.py --venue oct-sf-hackathon                      # dry run: plan only
  check-depth-alignment.py --venue oct-sf-hackathon --only sfhack9 --move
  check-depth-alignment.py --venue oct-sf-hackathon --move --yes         # whole fleet

WHAT IT DECIDES
---------------
`cam` and `gripper` hang off the arm FLANGE, so the configured `cam` frame is the thing
under test. There are only ever TWO candidates for a given cell, and they differ by
exactly Rz(180 deg) about the flange Z (verified against the repo fragments:
gripper2_t == Rz180 @ original_t and gripper2_R == Rz180 @ original_R).

That single fact drives everything:

    P_computed - f = R_err . (P_true - f)    R_err = R_flange . Rz180 . R_flange^T
                                             f    = the flange origin in world

i.e. if the configured mounting is WRONG, the world cloud you compute is the true scene
rotated 180 deg about the TOOL-AXIS LINE through the flange.

THE TRAP THIS SCRIPT EXISTS TO AVOID
------------------------------------
Rotating a horizontal plane 180 deg about a VERTICAL line gives back the same plane. So
with the tool pointing straight down, a wrong mounting reproduces the table at exactly
the right height with zero tilt. Pointing at the table and checking height detects
NOTHING, no matter how many tool-down poses you average. (CLAUDE.md's "168 mm" is how far
the CAMERA moves between mountings - not the scene.) Confirmed on hardware: at the
tool-down pose sfhack7 reported 2.19 deg for the configured frame and 2.17 deg for the
flipped one - indistinguishable.

Tilt the tool by alpha and the apparent table tilt under the wrong hypothesis is exactly
2*alpha (the normal maps to 2(a.z)a - z). At alpha = 25 deg that is 50 deg vs 0 deg - a
classification, not a tolerance, so depth noise is irrelevant to the verdict.

So instead of "is the measured table within N mm of where we think it is", we ask
geometry.md's question - compute where each candidate puts the scene and see which one
is physically possible:

    hypothesis CONFIG : the cloud as transform_pcd returns it
    hypothesis FLIP   : that cloud rotated by R_err about the flange

Exactly one should show a horizontal table.

    tilt_config < FLAT and tilt_flip > STEEP  -> PASS
    tilt_flip   < FLAT and tilt_config > STEEP -> FAIL, mounting is the other candidate
    both flat                                  -> ERROR (pose did not discriminate)
    neither flat                               -> FAIL (arm model, table, or calibration)

A tool-DOWN pose is still taken, but only for the absolute-height check, which catches a
wrong arm model (xArm6 vs xArm850 base heights differ ~97 mm) or a wrong table-translation.
It is explicitly NOT a mounting test.

WHAT THIS DOES NOT TEST: a base yaw error (base-axis-rotation-degrees) or a base x/y
offset leave a horizontal table horizontal, so they are invisible here. Only the camera
mounting and the base HEIGHT are decided.

SAFETY
------
Obstacles are a planning constraint, not a safety interlock (CLAUDE.md), and the arm
fragments ship collision_sensitivity: 0. Therefore:
  * every pose stays inside a 400 mm radius cylinder around the base, so even a wrong
    base-axis-rotation-degrees cannot aim the arm at a wall;
  * the table height is read from each machine's own frame system + geometry;
  * Ctrl-C calls RobotClient.stop_all() on every connected machine - it never starts a
    new motion, and a second Ctrl-C kills the script;
  * --move plus a typed confirmation listing the machines is required to move anything.

OPERATIONAL NOTE
----------------
Removing components from a machine's config does NOT reliably rebuild the motion
service's frame system. A cell can look entirely healthy - resources READY, frames clean,
config correct - and still refuse every planned move with
"Some frames are not linked to the world frame. Unlinked parts: [...]".
The fix is `viam machines part restart --part <id>`, not a config hunt. If you have just
cleared a machine, restart its part before running this.
"""
import argparse, asyncio, importlib.util, json, math, os, pathlib, re, sys

import numpy as np

HACK = pathlib.Path(__file__).resolve().parent.parent
ROOT = HACK.parent
OUT = HACK / "out"

FLAT_DEG = 5.0       # a correctly-resolved table should be flatter than this
STEEP_DEG = 20.0     # the losing hypothesis must be at least this tilted to be conclusive
MIN_POINTS = 800     # fewer than this in the table window -> ERROR, not FAIL
MIN_INLIER_FRAC = 0.55
NEAR_CAM_MM = 250.0  # drop points this close to the FLANGE (gripper claws, D435 min-Z)
MAX_RADIUS_MM = 400.0

# Flange poses: position mm + tool-axis direction (OV axis), theta 0.
# Reach-checked for xArm6 (the shorter arm) and clear of wall-back / wall-left.
POSES = [
    {"name": "down",     "pos": (350.0,   0.0, 400.0), "axis": (0.0, 0.0, -1.0),      "mount_test": False},
    {"name": "tilt-fwd", "pos": (300.0,   0.0, 420.0), "axis": (0.423, 0.0, -0.906),  "mount_test": True},
    {"name": "tilt-rgt", "pos": (300.0, 100.0, 420.0), "axis": (0.0, -0.423, -0.906), "mount_test": True},
]

C = {"g": "\033[32m", "r": "\033[31m", "y": "\033[33m", "b": "\033[1m", "d": "\033[2m", "x": "\033[0m"}


def paint(on):
    if not on:
        for k in C:
            C[k] = ""


# ----------------------------------------------------------------- small linear algebra
def _load_identify():
    """Reuse scripts/identify-config.py's orientation math rather than copying it.

    verify-cell.py does the same. One definition of the OV convention for the repo: if it
    ever changes, this script changes with it instead of diverging silently.
    """
    spec = importlib.util.spec_from_file_location(
        "identify_config", ROOT / "scripts" / "identify-config.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_IC = None


def ov_to_R(ox, oy, oz, th_deg):
    """Viam orientation vector -> rotation matrix (numpy), via identify-config.py."""
    global _IC
    if _IC is None:
        _IC = _load_identify()
    return np.asarray(_IC.ov_to_R(ox, oy, oz, th_deg))


def axis_to_ov(axis):
    """Tool-axis direction -> (o_x, o_y, o_z, theta=0)."""
    n = math.sqrt(sum(c * c for c in axis))
    return (axis[0] / n, axis[1] / n, axis[2] / n, 0.0)


# ----------------------------------------------------------------- PCD read / write
def parse_pcd(blob):
    """Minimal PCD reader -> numpy (N,3) of the x/y/z fields. ascii and binary."""
    nl = blob.find(b"DATA")
    if nl < 0:
        raise ValueError("not a PCD (no DATA line)")
    eol = blob.find(b"\n", nl)
    header = blob[:eol].decode("ascii", "replace")
    body = blob[eol + 1:]
    h = {}
    for line in header.splitlines():
        parts = line.split()
        if len(parts) >= 2 and not line.startswith("#"):
            h[parts[0].upper()] = parts[1:]
    fields = [f.lower() for f in h.get("FIELDS", [])]
    sizes = [int(v) for v in h.get("SIZE", [])]
    types = [t.upper() for t in h.get("TYPE", [])]
    counts = [int(v) for v in h.get("COUNT", ["1"] * len(fields))]
    data = h.get("DATA", ["ascii"])[0].lower()
    npts = int(h.get("POINTS", [0])[0] or 0) or (int(h.get("WIDTH", [0])[0]) * int(h.get("HEIGHT", [1])[0]))
    try:
        ix, iy, iz = fields.index("x"), fields.index("y"), fields.index("z")
    except ValueError:
        raise ValueError(f"PCD has no x/y/z fields: {fields}")

    if data == "ascii":
        rows = []
        for line in body.decode("ascii", "replace").splitlines():
            p = line.split()
            if len(p) < len(fields):
                continue
            rows.append((float(p[ix]), float(p[iy]), float(p[iz])))
        return np.asarray(rows, dtype=float)

    if data != "binary":
        raise ValueError(f"unsupported PCD DATA: {data}")

    stride = sum(s * c for s, c in zip(sizes, counts))
    off, offsets = 0, []
    for s, c in zip(sizes, counts):
        offsets.append(off)
        off += s * c
    fmt = {("F", 4): "f", ("F", 8): "d", ("I", 4): "i", ("U", 4): "I", ("I", 2): "h", ("U", 2): "H"}
    n_avail = len(body) // stride
    npts = min(npts or n_avail, n_avail)
    arr = np.frombuffer(body[: npts * stride], dtype=np.uint8).reshape(npts, stride)
    out = np.empty((npts, 3), dtype=float)
    for j, idx in enumerate((ix, iy, iz)):
        code = fmt.get((types[idx], sizes[idx]))
        if code is None:
            raise ValueError(f"unsupported PCD field type {types[idx]}{sizes[idx]}")
        seg = arr[:, offsets[idx]: offsets[idx] + sizes[idx]].tobytes()
        out[:, j] = np.frombuffer(seg, dtype=np.dtype("<" + code))
    return out


def write_pcd(pts):
    """numpy (N,3) -> binary PCD bytes, so we can thin a cloud before transform_pcd."""
    pts = np.asarray(pts, dtype="<f4")
    n = len(pts)
    header = (
        "VERSION .7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\n"
        f"WIDTH {n}\nHEIGHT 1\nVIEWPOINT 0 0 0 1 0 0 0\nPOINTS {n}\nDATA binary\n"
    )
    return header.encode("ascii") + pts.tobytes()


def to_mm(pts):
    """RDK writes PCD in METRES while the frame system is mm. Detect and scale.

    Rather than trust either, look at the data: a wrist camera 0.3-1.5 m from the scene
    gives |coord| of order 1 in metres and order 1000 in mm. Ambiguity is impossible by
    three orders of magnitude.
    """
    if len(pts) == 0:
        return pts, 1.0
    scale = 1000.0 if np.nanpercentile(np.abs(pts), 95) < 20.0 else 1.0
    return pts * scale, scale


# ----------------------------------------------------------------- plane fitting
def fit_plane(pts, iters=120, thresh=8.0, seed=0):
    """RANSAC plane -> (unit normal, point_on_plane, inlier_fraction). None if degenerate."""
    n = len(pts)
    if n < 50:
        return None
    rng = np.random.default_rng(seed)
    best, best_inl = None, -1
    for _ in range(iters):
        idx = rng.choice(n, 3, replace=False)
        p0, p1, p2 = pts[idx]
        nrm = np.cross(p1 - p0, p2 - p0)
        ln = np.linalg.norm(nrm)
        if ln < 1e-9:
            continue
        nrm = nrm / ln
        d = np.abs((pts - p0) @ nrm)
        inl = int((d < thresh).sum())
        if inl > best_inl:
            best_inl, best = inl, (nrm, p0)
    if best is None:
        return None
    nrm, p0 = best
    d = np.abs((pts - p0) @ nrm)
    keep = pts[d < thresh]
    if len(keep) < 30:
        return None
    c = keep.mean(axis=0)                        # least-squares refit on the inliers
    _, _, vt = np.linalg.svd(keep - c)
    nrm = vt[2] / np.linalg.norm(vt[2])
    return nrm, c, best_inl / n


def tilt_from_vertical(nrm):
    return math.degrees(math.acos(min(1.0, abs(float(np.asarray(nrm)[2])))))


def rotate_about(pts, R, origin):
    return (np.asarray(pts) - origin) @ np.asarray(R).T + origin


# ----------------------------------------------------------------- credentials / fleet
def load_creds():
    kid, key = os.environ.get("VIAM_API_KEY_ID"), os.environ.get("VIAM_API_KEY")
    if kid and key:
        return kid, key
    f = ROOT / "orgkey.txt"
    if f.exists():
        env = {}
        for line in f.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
        kid, key = env.get("VIAM_API_KEY_ID"), env.get("VIAM_API_KEY")
        if kid and key:
            return kid, key
    sys.exit("no credentials: set VIAM_API_KEY_ID / VIAM_API_KEY, or run "
             "hackathon/scripts/bootstrap-laptop.sh to write orgkey.txt")


async def list_machines(app, location_id):
    """[(name, part_id, fqdn)] for the venue location, numerically sorted."""
    out = []
    for r in await app.list_robots(location_id=location_id):
        parts = await app.get_robot_parts(r.id)
        main = next((p for p in parts if getattr(p, "main_part", False)), parts[0] if parts else None)
        if main is not None:
            out.append((r.name, main.id, main.fqdn))

    def key(n):
        m = re.search(r"(\d+)$", n[0])
        return (int(m.group(1)) if m else 10 ** 6, n[0])
    return sorted(out, key=key)


# ----------------------------------------------------------------- per-machine work
class Outcome:
    def __init__(self, name):
        self.name, self.verdict, self.detail, self.rows = name, "ERROR", "", []
        self.table_z, self.left_at = None, None


async def table_top_from_machine(robot, fallback):
    """Top face of this machine's own table obstacle, in world mm.

    The height check should test what THIS planner believes, not a fleet-wide constant:
    provision-viam.py takes --table-z per station.

    get_frame_system_config() gives the frame origin but leaves physical_object EMPTY for
    these obstacle components (only cam populates it), so the box size has to come from
    GetGeometries on the component itself - which does return it. Measured on sfhack7:
    frame z=-95, geometry 3000x3000x200 -> top at +5. geometry.md: frames carry box
    CENTERS, so the top face is center.z + height/2.
    """
    from viam.components.gripper import Gripper
    try:
        # The resource cache is not reliably populated the moment at_address() returns,
        # and `table` is looked up by NAME - refresh before asking for it, or this fails
        # with ResourceNotFoundError ('gripper','table').
        try:
            await robot.refresh()
        except Exception:
            pass
        z = None
        for f in await robot.get_frame_system_config():
            if f.frame.reference_frame == "table":
                z = f.frame.pose_in_observer_frame.pose.z
                break
        if z is None:
            return fallback, "venue file (no table frame)"
        try:
            for g in await Gripper.from_robot(robot, "table").get_geometries():
                if g.box.dims_mm.z:
                    return z + g.center.z + g.box.dims_mm.z / 2.0, "frame system + geometry"
        except Exception:
            pass
        return z + 100.0, "frame system (box size unavailable, assumed 200mm)"
    except Exception:
        pass
    return fallback, "venue file"


async def capture_world_cloud(robot, cam, thin, timeout):
    """point cloud -> thinned -> server-side frame transform -> world points in mm.

    The cloud is left in PCD's native metres for transform_pcd (RDK converts internally);
    only the returned cloud is scaled to mm for our own maths.
    """
    blob, _ = await asyncio.wait_for(cam.get_point_cloud(), timeout=timeout)
    pts = parse_pcd(blob)
    pts = pts[np.isfinite(pts).all(axis=1)]
    if len(pts) == 0:
        raise RuntimeError("camera returned an empty point cloud")
    if thin > 1:
        pts = pts[::thin]
    world_blob = await asyncio.wait_for(
        robot.transform_pcd(write_pcd(pts), "cam", "world"), timeout=timeout)
    world = parse_pcd(world_blob)
    world = world[np.isfinite(world).all(axis=1)]
    world, _ = to_mm(world)
    return world, len(blob)


def analyse(world, flange_pos, flange_R, table_z):
    """Fit the table under both mounting hypotheses. Returns a dict or raises."""
    # window: the bare table around the aim point, excluding the base column and
    # anything close to the FLANGE (the gripper claws leak in at the frame edges)
    d_cam = np.linalg.norm(world - flange_pos, axis=1)
    m = (d_cam > NEAR_CAM_MM) & (np.linalg.norm(world[:, :2], axis=1) > 160.0)
    m &= (world[:, 2] > table_z - 250.0) & (world[:, 2] < table_z + 250.0)
    cfg = world[m]
    if len(cfg) < MIN_POINTS:
        raise RuntimeError(f"only {len(cfg)} usable points in the table window (need {MIN_POINTS})")

    Rz180 = ov_to_R(0, 0, 1, 180)
    R_err = flange_R @ Rz180 @ flange_R.T          # 180 deg about the tool-axis LINE
    flip = rotate_about(cfg, R_err, flange_pos)

    out = {}
    for label, P in (("cfg", cfg), ("flip", flip)):
        f = fit_plane(P)
        if f is None:
            raise RuntimeError(f"could not fit a plane to the {label} hypothesis")
        nrm, c, frac = f
        out[label] = {"tilt": tilt_from_vertical(nrm), "z": float(c[2]), "inliers": frac}
    out["n"] = len(cfg)
    # plane fitting is rotation-invariant, so both hypotheses give the same inlier
    # fraction; testing one is enough.
    if out["cfg"]["inliers"] < MIN_INLIER_FRAC:
        raise RuntimeError(f"table window is cluttered (inliers {out['cfg']['inliers']:.0%})")
    return out


async def restore(arm, start_joints, args):
    """Put the arm back only when it is a small, safe move.

    move_to_joint_positions ignores obstacles, so it is only acceptable over a short
    delta. Models rest at opposite-signed J2 (xArm6 ~ -80, xArm850 ~ +85), so there is
    no fleet-wide 'home' to send everyone to.
    """
    from viam.proto.component.arm import JointPositions
    if start_joints is None:
        return False
    now = list((await arm.get_joint_positions()).values)
    if len(now) != len(start_joints):
        return False
    if max(abs(a - b) for a, b in zip(now, start_joints)) > args.restore_max_delta:
        return False      # too far to move obstacle-blind; caller reports where it sits
    await asyncio.wait_for(arm.move_to_joint_positions(JointPositions(values=start_joints)),
                           timeout=args.move_timeout)
    return True


def verdict(rows, table_z, args):
    """Combine the per-pose hypothesis fits into one answer."""
    mt = [r for r in rows if r["mount_test"]]
    if not mt:
        return "ERROR", "no discriminating pose completed"
    tl = " ".join(f"{r['pose']}:cfg{r['cfg']['tilt']:.0f}/flip{r['flip']['tilt']:.0f}" for r in mt)

    # The two hypotheses differ by 2*alpha (~50 deg) REGARDLESS of how the table itself
    # sits, so decide the mounting on the SEPARATION and the sign, not on an absolute
    # threshold. A table that is genuinely tilted (an uneven plate) then still gets the
    # right mounting verdict, and its tilt is reported separately instead of masquerading
    # as "neither hypothesis is flat".
    seps = [abs(r["cfg"]["tilt"] - r["flip"]["tilt"]) for r in mt]
    if min(seps) < STEEP_DEG:
        return "ERROR", (f"poses did not discriminate (separation {min(seps):.0f}deg < "
                         f"{STEEP_DEG:.0f}) - tool too close to vertical [{tl}]")
    cfg_wins = all(r["cfg"]["tilt"] < r["flip"]["tilt"] for r in mt)
    flip_wins = all(r["flip"]["tilt"] < r["cfg"]["tilt"] for r in mt)
    if flip_wins:
        return "FAIL", f"mounting is the OTHER candidate - swap the fragment [{tl}]"
    if not cfg_wins:
        return "ERROR", f"poses disagree on which hypothesis is flatter [{tl}]"

    worst = max(r["cfg"]["tilt"] for r in mt)
    if worst > FLAT_DEG:
        return "FAIL", (f"mounting is right but the table reads {worst:.0f}deg off level - "
                        f"uneven table, or arm base not square to it [{tl}]")
    down = [r for r in rows if not r["mount_test"]]
    if down:
        dz = down[0]["cfg"]["z"] - table_z
        if abs(dz) > args.tol_z:
            return "FAIL", (f"mounting OK but table is {dz:+.0f}mm off "
                            f"(arm model or table-translation?) [{tl}]")
    return "PASS", tl


async def run_machine(name, fqdn, kid, key, args, table_z_default, stop_flag, active):
    from viam.robot.client import RobotClient
    from viam.components.arm import Arm
    from viam.components.camera import Camera
    from viam.services.motion import Motion
    from viam.proto.common import Pose, PoseInFrame
    from viam.proto.robot import ResourceStatus

    res = Outcome(name)
    opts = RobotClient.Options.with_api_key(api_key=key, api_key_id=kid)
    opts.dial_options.timeout = args.dial_timeout
    try:
        robot = await asyncio.wait_for(RobotClient.at_address(fqdn, opts), timeout=args.dial_timeout + 10)
    except Exception as e:
        res.detail = f"connect failed: {type(e).__name__}"
        return res

    active.add(robot)
    arm = None
    start_joints = None
    allow_restore = True
    reached = None
    try:
        st = await robot.get_machine_status()
        states = {r.name.name: ResourceStatus.State.Name(r.state) for r in st.resources}
        missing = [n for n in ("arm", "cam") if n not in states]
        if missing:
            res.detail = f"missing resource(s): {','.join(missing)}"
            return res
        notready = [f"{n}={states[n]}" for n in ("arm", "cam") if "READY" not in states[n]]
        if notready:
            # the runbook's failure table keys on the controller's own words
            # ("Emergency Stop Button Pushed In", "devices found: 0") - surface them.
            errs = [str(r.error)[:120] for r in st.resources
                    if r.name.name in ("arm", "cam") and getattr(r, "error", None)]
            res.detail = "not ready: " + ",".join(notready) + (("  " + " | ".join(errs)) if errs else "")
            return res

        arm = Arm.from_robot(robot, "arm")
        cam = Camera.from_robot(robot, "cam")
        motion = Motion.from_robot(robot, "builtin")
        if args.table_top_z is not None:
            table_z, src = args.table_top_z, "--table-top-z"
        else:
            table_z, src = await table_top_from_machine(robot, table_z_default)
        res.table_z = table_z

        if await arm.is_moving():
            res.detail = "arm is already moving - someone is using this cell"
            return res
        start_joints = list((await arm.get_joint_positions()).values)

        if not args.move:
            res.verdict, res.detail = "DRY", (f"connected; arm+cam READY; table top "
                                              f"z={table_z:.0f} ({src}); {len(POSES)} poses would be sent")
            return res

        for spec in POSES:
            if stop_flag.is_set():
                res.detail = "aborted"
                return res
            ox, oy, oz, th = axis_to_ov(spec["axis"])
            goal = PoseInFrame(reference_frame="world",
                               pose=Pose(x=spec["pos"][0], y=spec["pos"][1], z=spec["pos"][2],
                                         o_x=ox, o_y=oy, o_z=oz, theta=th))
            try:
                ok = await asyncio.wait_for(motion.move(component_name="arm", destination=goal),
                                            timeout=args.move_timeout)
            except asyncio.TimeoutError:
                # mid-trajectory: stop, and never follow it with an obstacle-blind restore
                allow_restore = False
                try:
                    await robot.stop_all()
                except Exception:
                    pass
                res.detail = f"{spec['name']}: move timed out after {args.move_timeout}s; arm STOPPED"
                return res
            except Exception as e:
                res.detail = f"{spec['name']}: move failed: {str(e)[:90]}"
                return res
            if not ok:
                res.detail = f"{spec['name']}: motion service could not plan"
                return res
            reached = spec["name"]
            await asyncio.sleep(args.settle)

            # Use the MEASURED flange pose, not the commanded one.
            fp = await robot.transform_pose(
                PoseInFrame(reference_frame="arm", pose=Pose(o_z=1.0)), "world")
            p = fp.pose
            flange_pos = np.array([p.x, p.y, p.z])
            flange_R = ov_to_R(p.o_x, p.o_y, p.o_z, p.theta)
            err_mm = float(np.linalg.norm(flange_pos - np.array(spec["pos"])))
            if err_mm > args.pose_tol:
                res.detail = f"{spec['name']}: arrived {err_mm:.0f}mm from target"
                return res
            # Orientation matters as much as position: the discriminator is 2*alpha, so
            # landing at a different tilt changes what "steep" means.
            want = np.array(axis_to_ov(spec["axis"])[:3])
            got = flange_R[:, 2]
            ax_err = math.degrees(math.acos(min(1.0, max(-1.0, float(got @ want)))))
            if ax_err > args.axis_tol:
                res.detail = f"{spec['name']}: tool axis {ax_err:.0f}deg off target"
                return res
            if stop_flag.is_set():
                res.detail = "aborted"
                return res

            try:
                world, nbytes = await capture_world_cloud(robot, cam, args.thin, args.capture_timeout)
                a = analyse(world, flange_pos, flange_R, table_z)
            except asyncio.TimeoutError:
                res.detail = (f"{spec['name']}: point cloud timed out after "
                              f"{args.capture_timeout}s - raise --capture-timeout or --thin")
                return res
            except Exception as e:
                res.detail = f"{spec['name']}: {str(e)[:100]}"
                return res
            a["pose"] = spec["name"]
            a["mount_test"] = spec["mount_test"]
            res.rows.append(a)

        res.verdict, res.detail = verdict(res.rows, table_z, args)
        return res
    except asyncio.CancelledError:
        # Cancellation must not be followed by a new motion.
        stop_flag.set()
        raise
    except Exception as e:
        res.detail = f"{type(e).__name__}: {str(e)[:100]}"
        return res
    finally:
        # discard AFTER the restore: on_sigint only stops robots still in `active`, and a
        # restore is itself an obstacle-blind motion we must be able to interrupt.
        if arm is not None and args.move and args.restore and allow_restore and not stop_flag.is_set():
            try:
                if not await restore(arm, start_joints, args):
                    res.left_at = reached      # where it ACTUALLY is, not POSES[-1]
            except Exception:
                pass
        active.discard(robot)
        try:
            await robot.close()
        except Exception:
            pass


# ----------------------------------------------------------------- pose sanity
def validate_poses():
    """Poses must be safe even if the arm frame under test is wrong.

    base-axis-rotation-degrees is one of the things a wrong config could get wrong, so a
    pose must not rely on it. Keeping every pose inside a 400 mm cylinder means that under
    ANY yaw error the nearest wall face (wall-left y=625, wall-back x=-525) is still clear.
    """
    bad = []
    for p in POSES:
        r = math.hypot(p["pos"][0], p["pos"][1])
        if r > MAX_RADIUS_MM:
            bad.append(f"{p['name']}: radius {r:.0f}mm > {MAX_RADIUS_MM:.0f}mm")
        if not (250.0 <= p["pos"][2] <= 600.0):
            bad.append(f"{p['name']}: z {p['pos'][2]:.0f} outside 250-600mm")
        if p["mount_test"]:
            a = axis_to_ov(p["axis"])
            alpha = math.degrees(math.acos(min(1.0, abs(a[2]))))
            if alpha < 15.0:
                bad.append(f"{p['name']}: tool only {alpha:.0f}deg from vertical; a mounting "
                           f"error would show as {2*alpha:.0f}deg and may not clear "
                           f"{STEEP_DEG:.0f}deg")
    if bad:
        sys.exit("pose table is unsafe/ineffective:\n  " + "\n  ".join(bad))


async def amain():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--venue", required=True, help="slug of hackathon/venues/<slug>.json")
    p.add_argument("--only", help="comma-separated machine names to include")
    p.add_argument("--skip", help="comma-separated machine names to exclude")
    p.add_argument("--move", action="store_true",
                   help="actually move the arms. Without it this is a dry run.")
    p.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    p.add_argument("--no-restore", dest="restore", action="store_false",
                   help="leave arms at the last pose instead of returning them")
    p.add_argument("--parallel", type=int, default=0,
                   help="max arms moving at once (0 = all, the default)")
    # NOT --table-z: provision-viam.py's --table-z is the box CENTER. Same directory,
    # 100mm apart, so the names must differ.
    p.add_argument("--table-top-z", type=float, default=None,
                   help="table TOP face in world mm. Default: read from each machine's own "
                        "frame system, falling back to the venue file (center + 100)")
    p.add_argument("--thin", type=int, default=40,
                   help="keep every Nth point (default 40, minimum 2)")
    p.add_argument("--axis-tol", type=float, default=5.0,
                   help="max tool-axis error on arrival, degrees")
    p.add_argument("--tol-z", type=float, default=30.0, help="absolute table height tolerance mm")
    p.add_argument("--pose-tol", type=float, default=25.0, help="max arrival error mm")
    p.add_argument("--settle", type=float, default=1.5, help="seconds to settle after a move")
    p.add_argument("--dial-timeout", type=float, default=20.0)
    p.add_argument("--move-timeout", type=float, default=300.0)
    # 45s was not enough once several machines captured at once - it timed out a good cell.
    p.add_argument("--capture-timeout", type=float, default=120.0)
    p.add_argument("--restore-max-delta", type=float, default=40.0,
                   help="only restore joints if every joint is within this many degrees")
    p.add_argument("--no-color", action="store_true")
    args = p.parse_args()
    paint(not args.no_color and sys.stdout.isatty())
    args.parallel = max(0, args.parallel)
    args.thin = max(2, args.thin)
    validate_poses()

    vf = HACK / "venues" / f"{args.venue}.json"
    if not vf.exists():
        sys.exit(f"no venue file {vf}")
    venue = json.loads(vf.read_text())
    if not venue["location"]["id"]:
        sys.exit(f"{vf.name} has no location id yet - run create-venue.py first")
    table_z_default = (args.table_top_z if args.table_top_z is not None
                       else venue["workcell"]["table-translation"]["z"] + 100.0)

    kid, key = load_creds()
    from viam.app.viam_client import ViamClient
    from viam.rpc.dial import DialOptions, Credentials
    client = await ViamClient.create_from_dial_options(DialOptions(
        auth_entity=kid, credentials=Credentials(type="api-key", payload=key)))
    try:
        machines = await list_machines(client.app_client, venue["location"]["id"])
        if args.only:
            want = {s.strip() for s in args.only.split(",") if s.strip()}
            machines = [m for m in machines if m[0] in want]
        if args.skip:
            drop = {s.strip() for s in args.skip.split(",") if s.strip()}
            machines = [m for m in machines if m[0] not in drop]
        if not machines:
            sys.exit("no machines selected")

        print(f"{C['b']}=== depth / mounting check: {venue['event']} ==={C['x']}")
        print(f"  machines : {len(machines)}  ({', '.join(m[0] for m in machines)})")
        print(f"  table top: z = {table_z_default:.0f} mm (per-machine value read from its frame system)")
        print(f"  poses    : " + ", ".join(
            f"{s['name']}{'' if s['mount_test'] else ' (height only)'}" for s in POSES))
        print(f"  mode     : {C['r']+'MOVE'+C['x'] if args.move else C['y']+'DRY RUN'+C['x']}"
              f"   parallel: {'all' if not args.parallel else args.parallel}")

        if args.move and not args.yes:
            print(f"\n{C['y']}  These {len(machines)} arms will move AT THE SAME TIME.{C['x']}")
            print("  Obstacles are a planning constraint, not a safety interlock, and these")
            print("  fragments ship collision_sensitivity: 0. Make sure every cell is clear")
            print("  of people and objects before continuing.")
            try:
                if input("  Type MOVE to continue: ").strip() != "MOVE":
                    sys.exit("aborted")
            except (EOFError, KeyboardInterrupt):
                sys.exit("\naborted")

        stop_flag = asyncio.Event()
        active = set()
        loop = asyncio.get_running_loop()

        def on_sigint():
            # Ctrl-C must HALT. stop_all() cancels outstanding operations and stops every
            # actuator, which also aborts the server-side Move - a flag alone would let the
            # current trajectory run to completion.
            stop_flag.set()
            print(f"\n{C['y']}  interrupt: stopping {len(active)} machine(s){C['x']}", flush=True)
            for r in list(active):
                asyncio.ensure_future(r.stop_all())
            try:                       # a second Ctrl-C must kill the script
                loop.remove_signal_handler(signal.SIGINT)
            except Exception:
                pass
        import signal
        try:
            loop.add_signal_handler(signal.SIGINT, on_sigint)
        except (NotImplementedError, AttributeError):
            pass   # Windows: the CancelledError path in run_machine still suppresses restore

        sem = asyncio.Semaphore(args.parallel) if args.parallel else None

        async def guarded(name, fqdn):
            if sem is None:
                return await run_machine(name, fqdn, kid, key, args, table_z_default, stop_flag, active)
            async with sem:
                return await run_machine(name, fqdn, kid, key, args, table_z_default, stop_flag, active)

        print("\n  running...\n")
        results = await asyncio.gather(*[guarded(n, f) for n, _, f in machines],
                                       return_exceptions=True)

        colour = {"PASS": C["g"], "FAIL": C["r"], "ERROR": C["y"], "DRY": C["d"]}
        n_pass = n_fail = n_err = 0
        for (name, _, _), r in zip(machines, results):
            if isinstance(r, BaseException):
                print(f"  {C['y']}{'ERROR':7s}{C['x']} {name:10s} {type(r).__name__}")
                n_err += 1
                continue
            col = colour.get(r.verdict, C["y"])
            tail = r.detail
            if r.left_at:
                tail += f"  [left at pose '{r.left_at}']"
            print(f"  {col}{r.verdict:7s}{C['x']} {r.name:10s} {C['d']}{tail}{C['x']}")
            if r.rows:
                # the only evidence available if a verdict looks wrong on the day
                OUT.mkdir(exist_ok=True)
                (OUT / f"depth-{r.name}.json").write_text(json.dumps(
                    {"machine": r.name, "verdict": r.verdict, "detail": r.detail,
                     "table_top_z": r.table_z, "poses": r.rows}, indent=2) + "\n")
            n_pass += r.verdict == "PASS"
            n_fail += r.verdict == "FAIL"
            n_err += r.verdict == "ERROR"

        print(f"\n  {C['g']}{n_pass} pass{C['x']}   {C['r']}{n_fail} fail{C['x']}   "
              f"{C['y']}{n_err} error{C['x']}   of {len(machines)}")
        if n_fail or n_err:
            print(f"  {C['d']}FAIL = the geometry disagrees. ERROR = could not decide "
                  f"(connectivity, planning, clutter).{C['x']}")
        return 1 if (n_fail or n_err) else 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(amain()))
    except KeyboardInterrupt:
        sys.exit(130)
