#!/usr/bin/env python3
"""Reset an arm farm machine to its open lab baseline.

Baseline (Shannon, 2026-11-19): the machine's fragments (arm/cam/gripper, workcell
obstacles, shell) with no fragment overrides, plus an `overview-cam` webcam and the
`viam:system-audio` microphone. Everything a previous event added is removed.

Dry run by default: prints what would be kept and removed. `--apply` saves the
current config to backups/<machine>-<utc>.json and then writes the new one.
The app's config history keeps the old version too.

The overview webcam's path comes from the machine's webcam discovery service
(viam:find-webcams) if it has one, else from --webcam-path. A RealSense is never
chosen as the overview cam.

Credentials: org-scoped key in VIAM_API_KEY_ID / VIAM_API_KEY (see orgkey.txt).
"""
import argparse
import asyncio
import copy
import datetime
import json
import os
import pathlib
import sys

OBSTACLES_FRAGMENT = "01ca42e4-78b9-425c-ae75-1101b9a7f3e8"
KEEP_COMPONENT_MODELS = {"viam:system-audio:microphone"}
KEEP_MODULE_IDS = {"viam:system-audio"}
OVERVIEW_CAM = "overview-cam"


def build_open_lab_config(cfg, webcam_path):
    """Return (new_config, removed) for an open lab baseline. Pure; no I/O."""
    if not any(f.get("id") == OBSTACLES_FRAGMENT for f in cfg.get("fragments") or []):
        raise ValueError("machine does not use the workcell obstacles fragment")
    removed = {"components": [], "services": [], "modules": [], "packages": [],
               "fragment_mods": bool(cfg.get("fragment_mods"))}
    new = {"fragments": copy.deepcopy(cfg.get("fragments") or [])}

    comps = []
    for c in cfg.get("components") or []:
        if c.get("model") in KEEP_COMPONENT_MODELS:
            comps.append(copy.deepcopy(c))
        elif c.get("name") != OVERVIEW_CAM:
            removed["components"].append(c.get("name"))
    if webcam_path:
        old = next((c for c in cfg.get("components") or [] if c.get("name") == OVERVIEW_CAM), {})
        attrs = {k: v for k, v in (old.get("attributes") or {}).items()
                 if k in ("format", "width_px", "height_px", "frame_rate")}
        attrs["video_path"] = webcam_path
        comps.insert(0, {"name": OVERVIEW_CAM, "api": "rdk:component:camera",
                         "model": "rdk:builtin:webcam", "attributes": attrs})
    elif any(c.get("name") == OVERVIEW_CAM for c in cfg.get("components") or []):
        removed["components"].append(OVERVIEW_CAM)
    if comps:
        new["components"] = comps

    removed["services"] = [s.get("name") for s in cfg.get("services") or []]
    mods = []
    for m in cfg.get("modules") or []:
        if m.get("module_id") in KEEP_MODULE_IDS:
            mods.append(copy.deepcopy(m))
        else:
            removed["modules"].append(m.get("name"))
    if mods:
        new["modules"] = mods
    removed["packages"] = [p.get("name") for p in cfg.get("packages") or []]
    return new, removed


def pick_webcam(discovered):
    """From discovery results [(model, attrs)], the first non-RealSense webcam path."""
    for model, attrs in discovered:
        path = (attrs or {}).get("video_path", "")
        if model == "rdk:builtin:webcam" and path and "RealSense" not in path \
                and not path.startswith("pci-"):
            return path
    return None


async def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", required=True, help="machine name, for example armfarm22")
    ap.add_argument("--webcam-path", help="overview webcam video_path, if discovery is unavailable")
    ap.add_argument("--no-webcam", action="store_true", help="drop overview-cam")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    from viam.app.viam_client import ViamClient
    from viam.robot.client import RobotClient
    from viam.rpc.dial import DialOptions
    from viam.services.discovery import DiscoveryClient

    kid, key = os.environ.get("VIAM_API_KEY_ID"), os.environ.get("VIAM_API_KEY")
    if not (kid and key):
        sys.exit("set VIAM_API_KEY_ID / VIAM_API_KEY (org-scoped)")
    fleet = json.load(open(pathlib.Path(__file__).parent.parent / "config" / "fleet.json"))
    client = await ViamClient.create_from_dial_options(DialOptions.with_api_key(key, kid))
    try:
        app = client.app_client
        robots = await app.list_robots(location_id=fleet["location"]["id"])
        robot = next((r for r in robots if r.name == a.name), None)
        if not robot:
            sys.exit(f"no machine named {a.name} in the arm farm location")
        parts = await app.get_robot_parts(robot.id)
        part = next(p for p in parts if p.main_part)
        cfg = part.robot_config

        webcam = None if a.no_webcam else a.webcam_path
        if not a.no_webcam and not webcam:
            disc = [s["name"] for s in cfg.get("services") or []
                    if s.get("model") == "viam:find-webcams:webcam-discovery"]
            if disc:
                r = await asyncio.wait_for(RobotClient.at_address(
                    part.fqdn, RobotClient.Options.with_api_key(key, kid)), 60)
                try:
                    res = await asyncio.wait_for(
                        DiscoveryClient.from_robot(r, disc[0]).discover_resources(), 30)
                    webcam = pick_webcam([(x.model, dict(x.attributes or {})) for x in res])
                finally:
                    await r.close()
            if not webcam:
                sys.exit("no overview webcam found; pass --webcam-path or --no-webcam")

        new, removed = build_open_lab_config(cfg, webcam)
        print(f"{a.name} (part {part.id})")
        print("  keep fragments:", [f["id"] for f in new["fragments"]])
        print("  keep components:", [c["name"] for c in new.get("components", [])])
        print("  overview-cam video_path:", webcam)
        print("  keep modules:", [m["name"] for m in new.get("modules", [])])
        for k, v in removed.items():
            print(f"  remove {k}:", v)
        if not a.apply:
            print("dry run; pass --apply to write")
            return
        bdir = pathlib.Path(__file__).parent.parent / "backups"
        bdir.mkdir(exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        bfile = bdir / f"{a.name}-{stamp}.json"
        bfile.write_text(json.dumps(cfg, indent=1, default=str))
        print("  backup:", bfile)
        await app.update_robot_part(part.id, part.name, new)
        print("  applied")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
