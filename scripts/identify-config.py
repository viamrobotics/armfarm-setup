#!/usr/bin/env python3
"""Work out which of the four arm-farm configurations a machine is.

Two independent questions:

  1. ARM MODEL   xArm6 vs xArm850   - ask the person; the 850 is visibly longer
                                      reach, and the controller label says which.
  2. MOUNTING    original vs gripper2 - decided by WHERE THE CAMERA PHYSICALLY IS.
                                      This script does that math.

The two mountings put the camera ~168mm apart, roughly horizontally when the tool
points down. That is trivially eyeball-able; camera *orientation* is not. So never
guess at orientation - settle it on position and let the frames follow.

Get the arm's live pose first (GetEndPosition on the arm) and pass it in:

  identify-config.py --pose 391.7 5.3 305.5 -0.1224 0.0105 -0.9924 178.647

Then ask the person which of the two printed positions the camera is actually at.
"""
import argparse, math, re

# cam translation in the arm-flange frame, per mounting
CAM_T = {
    "original": (83.0, -14.0, 18.0),    # gripper upside down  (ufactory-xarm6-realsense)
    "gripper2": (-83.0, 14.0, 18.0),    # gripper right-side up (xarm-realsense-gripper2)
}


def _mul(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _mv(A, v):
    return [sum(A[i][k] * v[k] for k in range(3)) for i in range(3)]


def ov_to_R(ox, oy, oz, th_deg):
    """Viam orientation vector -> rotation matrix.  R = Rz(lon).Ry(lat).Rz(th)"""
    n = math.sqrt(ox * ox + oy * oy + oz * oz)
    ox, oy, oz = ox / n, oy / n, oz / n
    lat = math.acos(max(-1.0, min(1.0, oz)))
    lon = 0.0 if (1 - abs(oz)) < 1e-9 else math.atan2(oy, ox)
    Rz = lambda a: [[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]]
    Ry = lambda a: [[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]]
    return _mul(_mul(Rz(lon), Ry(lat)), Rz(math.radians(th_deg)))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    # GetEndPosition routinely returns components like -2.7e-20. argparse only treats a
    # leading-dash token as a number if it matches its own matcher, which has no
    # exponent form - so scientific notation was parsed as an option name and the call
    # failed with "expected 7 arguments". Widen it to accept exponents.
    p._negative_number_matcher = re.compile(
        r"^-(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")
    p.add_argument("--pose", nargs="+", required=True,
                   metavar=("X", "Y Z OX OY OZ THETA"),
                   help="arm GetEndPosition: position mm, orientation vector, theta deg. "
                        "Seven numbers, or one quoted/comma-separated string.")
    a = p.parse_args()
    # Accept "--pose 1 2 3 ..." and "--pose '1 2 3 ...'" and commas, so a pose pasted
    # straight out of the app or a JSON response works without reformatting.
    vals = []
    for tok in a.pose:
        vals.extend(t for t in re.split(r"[,\s]+", tok.strip()) if t)
    if len(vals) != 7:
        p.error(f"--pose needs 7 numbers (x y z ox oy oz theta), got {len(vals)}")
    try:
        x, y, z, ox, oy, oz, th = (float(v) for v in vals)
    except ValueError as e:
        p.error(f"--pose values must all be numbers: {e}")
    R = ov_to_R(ox, oy, oz, th)

    print(f"flange at ({x:.1f}, {y:.1f}, {z:.1f}), tool axis "
          f"({_mv(R,[0,0,1])[0]:.3f}, {_mv(R,[0,0,1])[1]:.3f}, {_mv(R,[0,0,1])[2]:.3f})\n")
    print("Where the camera would be, per mounting:\n")
    pos = {}
    for name, t in CAM_T.items():
        off = _mv(R, list(t))
        pos[name] = [x + off[0], y + off[1], z + off[2]]
        print(f"  {name:9s} -> ({pos[name][0]:8.1f}, {pos[name][1]:8.1f}, {pos[name][2]:8.1f})")

    a_, b_ = pos["original"], pos["gripper2"]
    sep = math.dist(a_, b_)
    dx, dy = b_[0] - a_[0], b_[1] - a_[1]
    axis = "X (fore/aft)" if abs(dx) >= abs(dy) else "Y (left/right)"

    # "further from the base" is RADIAL distance from the base at the world origin,
    # never the sign of one world axis. With the arm reaching into -Y a +Y step moves
    # the camera TOWARD the base, so a signed-axis test inverts the answer and picks
    # the wrong mounting - the exact failure this script exists to prevent.
    base = (0.0, 0.0, 0.0)
    d_orig, d_grip = math.dist(base, a_), math.dist(base, b_)
    delta = d_grip - d_orig

    print(f"\n  separation: {sep:.0f} mm, mostly along {axis}")
    print(f"  distance from base: original {d_orig:.0f} mm, gripper2 {d_grip:.0f} mm\n")
    print("ASK THE PERSON AT THE MACHINE:")
    print("  Looking at the wrist, is the camera nearer the arm's base, or further from it?")
    print(f"    further from base  -> {'gripper2' if delta > 0 else 'original'}")
    print(f"    nearer the base    -> {'original' if delta > 0 else 'gripper2'}")
    print("\nThen pick the fragment: <model>-<mounting> in config/fleet.json.")


if __name__ == "__main__":
    main()
