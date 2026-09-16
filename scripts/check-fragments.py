#!/usr/bin/env python3
"""Assert the four arm fragments agree where they must.

cam and gripper are parented to the arm flange, so their frames depend ONLY on the
mounting - never on the arm model. Two fragments with the same mounting must therefore
carry byte-equivalent cam and gripper frames. This catches the drift that broke the
upstream ufactory-xarm6-realsense / -not-backwards pair.

Compares rotations as matrices, so quaternion and ov_degrees spellings of the same
rotation compare equal.

  VIAM_API_KEY_ID=... VIAM_API_KEY=... check-fragments.py
Exit status is nonzero if any pair disagrees, so it can gate CI.
"""
import asyncio, json, math, os, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FLEET = json.loads((ROOT / "config" / "fleet.json").read_text())
TOL = 1e-6


def _mul(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def ov_to_R(v):
    ox, oy, oz, th = float(v["x"]), float(v["y"]), float(v["z"]), float(v["th"])
    n = math.sqrt(ox * ox + oy * oy + oz * oz)
    ox, oy, oz = ox / n, oy / n, oz / n
    lat = math.acos(max(-1.0, min(1.0, oz)))
    lon = 0.0 if (1 - abs(oz)) < 1e-9 else math.atan2(oy, ox)
    Rz = lambda a: [[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]]
    Ry = lambda a: [[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]]
    return _mul(_mul(Rz(lon), Ry(lat)), Rz(math.radians(th)))


def quat_to_R(v):
    w, x, y, z = (float(v[k]) for k in "wxyz")
    n = math.sqrt(w * w + x * x + y * y + z * z)
    w, x, y, z = w / n, x / n, y / n, z / n
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]]


def to_R(orientation):
    v = orientation["value"]
    return quat_to_R(v) if orientation["type"] == "quaternion" else ov_to_R(v)


def frame_of(cfg, name):
    for c in (cfg or {}).get("components", []):
        if c.get("name") == name:
            return c.get("frame", {}) or {}
    return {}


def same(f1, f2):
    t1, t2 = f1.get("translation", {}), f2.get("translation", {})
    for k in "xyz":
        if abs(float(t1.get(k, 0)) - float(t2.get(k, 0))) > TOL:
            return False, f"translation {k}: {t1.get(k)} vs {t2.get(k)}"
    R1, R2 = to_R(f1["orientation"]), to_R(f2["orientation"])
    err = max(abs(R1[i][j] - R2[i][j]) for i in range(3) for j in range(3))
    if err > 1e-6:
        return False, f"rotation differs, max element error {err:.6f}"
    return True, "ok"


async def main():
    from viam.app.viam_client import ViamClient
    from viam.rpc.dial import DialOptions, Credentials
    kid, key = os.environ.get("VIAM_API_KEY_ID"), os.environ.get("VIAM_API_KEY")
    if not (kid and key):
        sys.exit("set VIAM_API_KEY_ID / VIAM_API_KEY")

    client = await ViamClient.create_from_dial_options(DialOptions(
        auth_entity=kid, credentials=Credentials(type="api-key", payload=key)))
    bad = 0
    try:
        cfgs = {}
        for key_, meta in FLEET["arm_configs"].items():
            fr = await client.app_client.get_fragment(fragment_id=meta["id"])
            cfgs[key_] = fr.fragment or {}
            arm = next((c for c in cfgs[key_].get("components", []) if c["name"] == "arm"), {})
            ok = arm.get("model") == meta["model"]
            print(f"  {key_:17s} {meta['name']:28s} arm model {arm.get('model')} "
                  f"{'ok' if ok else '*** EXPECTED ' + meta['model'] + ' ***'}")
            bad += 0 if ok else 1

        print()
        by_mount = {}
        for key_, meta in FLEET["arm_configs"].items():
            by_mount.setdefault(meta["mounting"], []).append(key_)
        for mounting, keys in sorted(by_mount.items()):
            if len(keys) < 2:
                print(f"  {mounting}: only {keys[0]}, nothing to compare")
                continue
            a, b = keys[0], keys[1]
            for part in ("cam", "gripper"):
                ok, why = same(frame_of(cfgs[a], part), frame_of(cfgs[b], part))
                print(f"  {mounting:9s} {part:8s} {a} vs {b}: {'MATCH' if ok else 'DRIFT - ' + why}")
                bad += 0 if ok else 1
    finally:
        client.close()
    print("\n" + ("OK" if not bad else f"{bad} problem(s)"))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    asyncio.run(main())
