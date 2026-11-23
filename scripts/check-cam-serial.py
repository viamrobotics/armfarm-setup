#!/usr/bin/env python3
"""Check each machine's RealSense serial against the camera that is plugged in.

For each machine named, the script:
  1. saves the current part config to backups/<machine>-<utc>.json,
  2. adds a temporary `viam:realsense:discovery` service to the part,
  3. asks it which RealSense cameras the machine can see,
  4. compares the discovered serial with the `cam-serial-number` variable on the
     machine's arm fragment and prints match or mismatch,
  5. removes the temporary service (always, even after an error).

Dry run by default: steps 2 and 5 still write the part config, but the serial is
left as it is. With --apply, a mismatch is fixed in step 5's write when exactly one
camera was discovered. Several cameras, or a camera in recovery mode, are reported
and never written.

Program-ops #10 found wrong serials on several arm farm machines; a wrong serial
shows as `failed to start device` on `cam`. A right serial with that error points
at the USB link instead (armfarm17 and armfarm18, 2026-11-20).

Credentials: VIAM_API_KEY_ID / VIAM_API_KEY, or orgkey.txt at the repo root
(scripts/bootstrap.sh writes it).

  ./scripts/check-cam-serial.py armfarm17 armfarm18
  ./scripts/check-cam-serial.py armfarm14 --apply
"""
import argparse
import asyncio
import copy
import datetime
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMP_SERVICE = "tmp-realsense-discovery"
SERIAL_VAR = "cam-serial-number"
REALSENSE_MODEL = "viam:camera:realsense"


def load_creds(root, environ):
    """(key id, key) from the environment, else from <root>/orgkey.txt."""
    kid, key = environ.get("VIAM_API_KEY_ID"), environ.get("VIAM_API_KEY")
    if kid and key:
        return kid, key
    f = pathlib.Path(root) / "orgkey.txt"
    if f.exists():
        env = dict(line.split("=", 1) for line in f.read_text().splitlines()
                   if "=" in line and not line.startswith("#"))
        if env.get("VIAM_API_KEY_ID") and env.get("VIAM_API_KEY"):
            return env["VIAM_API_KEY_ID"].strip(), env["VIAM_API_KEY"].strip()
    sys.exit("no credentials: set VIAM_API_KEY_ID / VIAM_API_KEY, or run scripts/bootstrap.sh")


def find_machine(robots, name):
    """The one machine called `name`. Two with the same name is an error, not a choice."""
    hits = [r for r in robots if r.name == name]
    if not hits:
        raise LookupError(f"no machine named {name} in the arm farm location")
    if len(hits) > 1:
        raise LookupError(f"{len(hits)} machines named {name} ({', '.join(r.id for r in hits)}); "
                          "rename or delete the stale one before checking")
    return hits[0]


def find_serial_variable(cfg, arm_fragment_ids):
    """(index into cfg['fragments'], current serial or None) for the arm fragment."""
    frags = cfg.get("fragments") or []
    for i, f in enumerate(frags):
        if SERIAL_VAR in (f.get("variables") or {}):
            return i, f["variables"][SERIAL_VAR]
    for i, f in enumerate(frags):
        if f.get("id") in arm_fragment_ids:
            return i, None
    raise ValueError("machine has no arm fragment from config/fleet.json and no fragment "
                     f"sets {SERIAL_VAR}")


def add_discovery_service(cfg, name):
    new = copy.deepcopy(cfg)
    services = new.setdefault("services", [])
    if any(s.get("name") == name for s in services):
        raise ValueError(f"a service named {name} already exists; remove it first")
    services.append({"name": name, "api": "rdk:service:discovery",
                     "model": "viam:realsense:discovery"})
    return new


def remove_service(cfg, name):
    new = copy.deepcopy(cfg)
    services = [s for s in new.get("services") or [] if s.get("name") != name]
    if services:
        new["services"] = services
    else:
        new.pop("services", None)
    return new


def with_serial(cfg, index, serial):
    new = copy.deepcopy(cfg)
    new["fragments"][index].setdefault("variables", {})[SERIAL_VAR] = serial
    return new


def discovered_serials(found):
    """From discovery results [(name, model, attrs)]: (serials, recovery-mode ids).

    The realsense module names a recovery/DFU-mode device realsense-<id>-recovery and
    puts its firmware update id, not a serial, in serial_number.
    """
    serials, recovery = [], []
    for name, model, attrs in found:
        if model != REALSENSE_MODEL:
            continue
        sn = str((attrs or {}).get("serial_number", ""))
        if not sn:
            continue
        (recovery if name.endswith("-recovery") else serials).append(sn)
    return serials, recovery


def verdict(configured, serials, recovery):
    """(status, serial to write or None, message)."""
    if len(serials) == 1:
        if serials[0] == configured:
            return "match", None, f"configured {configured} = discovered"
        return "mismatch", serials[0], f"configured {configured}, discovered {serials[0]}"
    if len(serials) > 1:
        if configured in serials:
            return "match", None, f"configured {configured} is one of {serials}"
        return "ambiguous", None, (f"configured {configured}, discovered {serials}; "
                                   "more than one camera, so the script will not choose")
    if recovery:
        return "recovery", None, f"a RealSense is in recovery mode ({recovery}); update its firmware"
    return "none", None, "no RealSense found; check the USB cable and the module logs"


async def discover(fqdn, kid, key, deadline_s):
    """Discovery results [(name, model, attrs)] from the temporary service."""
    from viam.errors import ResourceNotFoundError
    from viam.robot.client import RobotClient
    from viam.services.discovery import DiscoveryClient
    from viam.utils import struct_to_dict

    robot = await asyncio.wait_for(
        RobotClient.at_address(fqdn, RobotClient.Options.with_api_key(key, kid)), 60)
    try:
        end, last = time.monotonic() + deadline_s, None
        while time.monotonic() < end:
            # The machine reads the new config on its own schedule; poll until the
            # service exists and answers.
            try:
                await robot.refresh()
                svc = DiscoveryClient.from_robot(robot, TEMP_SERVICE)
                res = await svc.discover_resources(timeout=30)
                return [(r.name, r.model, struct_to_dict(r.attributes)) for r in res]
            except ResourceNotFoundError as e:
                last = e
            except Exception as e:  # service still starting
                last = e
            await asyncio.sleep(5)
        raise TimeoutError(f"{TEMP_SERVICE} did not answer in {deadline_s}s: {last}")
    finally:
        await robot.close()


async def check_one(app, robots, name, arm_fragment_ids, kid, key, apply, wait_s):
    robot = find_machine(robots, name)
    part = next(p for p in await app.get_robot_parts(robot.id) if p.main_part)
    cfg = part.robot_config
    index, configured = find_serial_variable(cfg, arm_fragment_ids)
    print(f"{name} (part {part.id}): configured {SERIAL_VAR} = {configured}")

    bdir = ROOT / "backups"
    bdir.mkdir(exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bfile = bdir / f"{name}-{stamp}.json"
    bfile.write_text(json.dumps(cfg, indent=1, default=str))
    print("  backup:", bfile)

    await app.update_robot_part(part.id, part.name, add_discovery_service(cfg, TEMP_SERVICE))
    print(f"  added temporary service {TEMP_SERVICE}")
    status, proposed, msg = "error", None, ""
    try:
        found = await discover(part.fqdn, kid, key, wait_s)
        status, proposed, msg = verdict(configured, *discovered_serials(found))
    except Exception as e:
        msg = f"discovery failed: {e}"
    finally:
        # Re-read so a change someone made in the last minute is kept, then drop only
        # the temporary service (and set the serial, if asked).
        parts = await app.get_robot_parts(robot.id)
        cur = next(p for p in parts if p.main_part).robot_config
        new = remove_service(cur, TEMP_SERVICE)
        wrote = ""
        if apply and proposed:
            idx, _ = find_serial_variable(new, arm_fragment_ids)
            new = with_serial(new, idx, proposed)
            wrote = f"; set {SERIAL_VAR} = {proposed}"
        await app.update_robot_part(part.id, part.name, new)
        print(f"  removed temporary service {TEMP_SERVICE}{wrote}")

    print(f"  {status.upper()}: {msg}")
    if proposed and not apply:
        print(f"  dry run; pass --apply to set {SERIAL_VAR} = {proposed}")
    return status


async def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("names", nargs="+", help="machine names, for example armfarm17")
    ap.add_argument("--apply", action="store_true",
                    help="write the discovered serial when it differs (one camera only)")
    ap.add_argument("--wait", type=int, default=120,
                    help="seconds to wait for the temporary service to answer")
    a = ap.parse_args()

    from viam.app.viam_client import ViamClient
    from viam.rpc.dial import DialOptions

    kid, key = load_creds(ROOT, os.environ)
    fleet = json.loads((ROOT / "config" / "fleet.json").read_text())
    arm_ids = {c["id"] for c in fleet["arm_configs"].values()}
    client = await ViamClient.create_from_dial_options(DialOptions.with_api_key(key, kid))
    results = {}
    try:
        app = client.app_client
        robots = await app.list_robots(location_id=fleet["location"]["id"])
        for name in a.names:
            try:
                results[name] = await check_one(app, robots, name, arm_ids, kid, key,
                                                a.apply, a.wait)
            except (LookupError, ValueError) as e:
                print(f"{name}: {e}")
                results[name] = "error"
    finally:
        client.close()
    print()
    for name, status in results.items():
        print(f"{name:12} {status}")
    sys.exit(0 if all(s == "match" or (a.apply and s == "mismatch") for s in results.values())
             else 1)


if __name__ == "__main__":
    asyncio.run(main())
