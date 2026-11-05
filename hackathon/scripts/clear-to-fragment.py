#!/usr/bin/env python3
"""Reset a machine to "just the fragment": arm, camera, gripper, table, ceiling, walls.

  clear-to-fragment.py --venue oct-sf-hackathon --all              # dry run
  clear-to-fragment.py --venue oct-sf-hackathon sfhack3 sfhack4 --apply
  clear-to-fragment.py --venue oct-sf-hackathon --all --apply --skip sfhack17

Hackathon cells accumulate: saved poses, crop boxes, one-off services, modules someone
tried. This puts a machine back to a known state - the model-matched arm fragment, the
venue obstacles fragment, and the two safety walls - and drops everything else.

WHAT IT KEEPS, AND WHY THAT MATTERS
-----------------------------------
Not every machine-level component is disposable. Two kinds look identical in the config
and are opposites in practice:

  * PROJECT work   - `home`, `drop_pose`, `table-crop`, `pick-bar-crop`. Safe to drop;
                     config history has it if someone wants it back.
  * HARDWARE facts - a `vacuum` component plus a `components.gripper.disabled` mod is
                     how a cell with a VACUUM end effector is described. Delete those and
                     the fragment's standard two-finger gripper comes back on an arm that
                     has not got one, and the controller answers
                     "End Module Communication Error" - which reads like a cable fault.

So --apply refuses to touch a machine carrying components this script does not recognise
as obstacles, unless you pass --force or name them in --keep. Look first, then decide.

For a VACUUM CELL the correct remedy is `--keep vacuum`, NOT --force. The
`components.gripper.disabled` mod is a hardware fact, so it is carried forward
automatically and re-emitted against the replacement fragment id. --force would drop it
and put the fragment's two-finger gripper back alongside the vacuum - the exact failure
described above. --force is for project components you have decided are disposable.

MODS CAN HOLD THE REAL HARDWARE IDENTITY
----------------------------------------
A fragment_mod overrides the fragment variable, so the mod is the live truth. Observed in
the field: a machine whose `arm-ip-address` variable was 192.168.1.1 (the gateway) and
whose `cam-serial-number` was "1", with the REAL values only in a mod. Clearing mods while
copying variables forward would have pointed its arm at the gateway and broken three
cameras. This script promotes `components.arm.attributes.host` and
`components.cam.attributes.serial_number` out of the mods into the variables first, and
prints every promotion it makes.

AFTERWARDS: RESTART THE PART
----------------------------
Removing components does not reliably rebuild the motion service's frame system. The cell
will look healthy - resources READY, frames clean, config correct - and refuse every
planned move with "Some frames are not linked to the world frame. Unlinked parts: [...]".
Pass --restart (or run `viam machines part restart --part <id>`) after clearing.
"""
import argparse, asyncio, json, os, pathlib, re, sys

HACK = pathlib.Path(__file__).resolve().parent.parent
ROOT = HACK.parent

OBSTACLE_MODEL = "erh:vmodutils:obstacle"
WALLS = {   # centers, per docs/geometry.md - NOT faces
    "wall-back": ((-550, 0, 488), (50, 1500, 1024)),
    "wall-left": ((0, 650, 488), (1500, 50, 1024)),
}


def wall(name):
    c, d = WALLS[name]
    return {"api": "rdk:component:gripper", "model": OBSTACLE_MODEL, "name": name,
            "attributes": {"geometries": [{"label": name, "type": "box",
                                           "x": d[0], "y": d[1], "z": d[2]}]},
            "frame": {"parent": "world",
                      "orientation": {"type": "ov_degrees", "value": {"x": 0, "y": 0, "z": 1, "th": 0}},
                      "translation": {"x": c[0], "y": c[1], "z": c[2]}},
            "ui_folder": {"name": "obstacles"}}


def load_creds():
    kid, key = os.environ.get("VIAM_API_KEY_ID"), os.environ.get("VIAM_API_KEY")
    if kid and key:
        return kid, key
    f = ROOT / "orgkey.txt"
    if f.exists():
        env = dict(l.split("=", 1) for l in f.read_text().splitlines() if "=" in l and not l.startswith("#"))
        if env.get("VIAM_API_KEY_ID") and env.get("VIAM_API_KEY"):
            return env["VIAM_API_KEY_ID"].strip(), env["VIAM_API_KEY"].strip()
    sys.exit("no credentials: set VIAM_API_KEY_ID / VIAM_API_KEY, or run bootstrap-laptop.sh")


def arm_fragment_map(venue):
    """{fragment_id: (model, replacement_id)} from the venue file.

    The venue carries these because they are per-event: a new event gets new fragments.
    `current` is what a cleared machine should end up on; `migrate_from` lists the older
    fragments that still identify the same arm model, so this script can recognise a
    machine that has not been cleared yet.
    """
    af = venue.get("arm_fragments")
    if not af:
        sys.exit(f"venue file has no 'arm_fragments' - add it (see hackathon/README.md)")
    out = {}
    for model, spec in af.items():
        if model.startswith("_"):          # _comment and friends
            continue
        cur = spec["current"]["id"]
        out[cur] = (model, cur)
        for old in spec.get("migrate_from", []):
            out[old["id"]] = (model, cur)
    return out, {m: v["current"] for m, v in af.items() if not m.startswith("_")}


async def clear_one(app, name, part_id, venue, armmap, current, args):
    part = await app.get_robot_part(part_id)
    cfg = part.robot_config
    if cfg is None:
        print(f"  {name:10s} SKIP - no robot_config")
        return False
    cfg = json.loads(cfg) if isinstance(cfg, str) else json.loads(json.dumps(cfg))
    ids = [f["id"] for f in cfg.get("fragments", [])]

    armf = [i for i in ids if i in armmap]
    if len(armf) != 1:
        print(f"  {name:10s} SKIP - {'no' if not armf else 'multiple'} recognised arm fragment")
        return False
    model, repl = armmap[armf[0]]
    av = dict(next(f.get("variables", {}) for f in cfg["fragments"] if f["id"] == armf[0]))

    # Mods override variables, so a mod is the live truth - promote before dropping them.
    promoted = []
    for fm in cfg.get("fragment_mods", []):
        for m in fm.get("mods", []):
            for k, val in (m.get("$set") or {}).items():
                if k == "components.arm.attributes.host" and val:
                    if av.get("arm-ip-address") != val:
                        promoted.append(f"arm-ip {av.get('arm-ip-address')} -> {val}")
                    av["arm-ip-address"] = val
                elif k == "components.cam.attributes.serial_number":
                    if av.get("cam-serial-number") != val:
                        promoted.append(f"cam-sn {av.get('cam-serial-number')!r} -> {val!r}")
                    av["cam-serial-number"] = val

    keep_names = set(args.keep or [])
    kept = [x for x in cfg.get("components", []) if x.get("name") in keep_names]
    unknown = [x.get("name") for x in cfg.get("components", [])
               if x.get("name") not in keep_names and x.get("name") not in WALLS
               and x.get("model") != OBSTACLE_MODEL]
    dropped = [x.get("name") for x in cfg.get("components", [])
               if x.get("name") not in keep_names and x.get("name") not in WALLS]

    # A mod can describe HARDWARE too (components.gripper.disabled on a vacuum cell), so
    # anything we did not promote is treated exactly like an unrecognised component.
    PROMOTABLE = {"components.arm.attributes.host", "components.cam.attributes.serial_number"}
    # Some mods are hardware facts we can neither promote into a variable nor discard:
    # a vacuum cell suppresses the fragment's two-finger gripper. Dropping that mod puts
    # the fragment's gripper back on a flange that does not have one - the sfhack14 break.
    # They are re-emitted against the REPLACEMENT fragment id, since the old id is gone.
    CARRYABLE = {"components.gripper.disabled"}
    leftover_mods, carry_mods = [], {}
    for fm in cfg.get("fragment_mods", []):
        for m in fm.get("mods", []):
            for op, body in m.items():
                for k, val in (body or {}).items():
                    if op == "$set" and k in PROMOTABLE:
                        continue
                    if op == "$set" and k in CARRYABLE:
                        carry_mods[k] = val
                        continue
                    leftover_mods.append(f"{op} {k}")

    obst = venue["obstacles_fragment"]["id"]
    ov = next((f.get("variables", {}) for f in cfg["fragments"] if f["id"] == obst), None)
    # Carry EVERY variable the machine had, not a whitelist: the replacement fragment may
    # require ones this script has never heard of, and a missing variable fails to render.
    newvars = dict(av)
    newvars.setdefault("arm-name", "arm")
    newvars.setdefault("base-axis-rotation-degrees", 0)
    newvars.setdefault("cam-serial-number", "")
    newvars.setdefault("translation", {"x": 0, "y": 0, "z": 0})
    frags = [{"id": repl, "variables": newvars},
             {"id": obst, "variables": ov or {
                  "table-translation": dict(venue["workcell"]["table-translation"]),
                  "ceiling-translation": dict(venue["workcell"]["ceiling-translation"])}}]
    for extra in args.keep_fragment or []:
        orig = next((f for f in cfg["fragments"] if f["id"] == extra), None)
        if orig is not None:
            frags.append(json.loads(json.dumps(orig)))   # keep its variables too

    # update_robot_part REPLACES the config, so anything top-level we do not carry is
    # destroyed - including `agent` (viam-agent's own settings), remotes, packages,
    # processes. Carry everything we are not deliberately rewriting.
    REWRITTEN = {"fragments", "components", "modules", "services", "fragment_mods"}
    carried = {k: v for k, v in cfg.items() if k not in REWRITTEN}
    walls = [wall(w) for w in sorted(WALLS) if w not in keep_names]
    new = dict(carried)
    newmods = ([{"fragment_id": repl, "mods": [{"$set": dict(carry_mods)}]}]
               if carry_mods else [])
    new.update({"fragments": frags,
                "components": kept + walls,
                "modules": [], "services": [], "fragment_mods": newmods})

    print(f"  {name:10s} {model:7s} -> {current[model]['name']}")
    print(f"             arm-ip={av.get('arm-ip-address','?')}  cam-sn={av.get('cam-serial-number','')!r}")
    for p in promoted:
        print(f"             PROMOTED {p}")
    for k, v in carry_mods.items():
        print(f"             CARRIED MOD {k}={v} -> fragment {repl[:8]}")
    print(f"             drop: {len(dropped)} components, {len(cfg.get('fragment_mods', []))} mods, "
          f"{len(cfg.get('modules', []))} modules, {len(cfg.get('services', []))} services")
    if dropped:
        print(f"             components: {', '.join(dropped[:10])}{' ...' if len(dropped) > 10 else ''}")
    if carried:
        print(f"             carrying top-level: {', '.join(sorted(carried))}")
    if kept:
        print(f"             keeping components: {', '.join(x['name'] for x in kept)}")
    ip = av.get("arm-ip-address", "")
    host = venue.get("network", {}).get("arm_host_ip_cidr", "192.168.1.150/24").split("/")[0]
    subnet = ".".join(host.split(".")[:3])
    bad_ip = (not ip or ip == host or ip.endswith(".1")
              or ".".join(ip.split(".")[:3]) != subnet)
    if bad_ip and not args.force:
        print(f"             REFUSED: arm-ip {ip!r} is empty, the host, the gateway, or off")
        print(f"                      the arm subnet {subnet}.x - provision-viam.py rejects these too.")
        return False
    if leftover_mods and not args.force:
        print(f"             REFUSED: fragment_mod(s) this script cannot carry: "
              f"{', '.join(sorted(set(leftover_mods))[:4])}")
        print(f"                      A mod can describe HARDWARE (e.g. components.gripper.disabled")
        print(f"                      on a vacuum cell). Inspect, then --force or --keep.")
        return False
    if unknown and not args.force:
        print(f"             REFUSED: unrecognised component(s) {unknown[:6]} - these may describe")
        print(f"                      HARDWARE (e.g. a vacuum gripper), not project work.")
        print(f"                      Inspect, then re-run with --force or --keep <name>.")
        return False
    if args.apply:
        await app.update_robot_part(robot_part_id=part_id, name=part.name, robot_config=new)
        print("             APPLIED")
    return True


async def amain():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("machines", nargs="*", help="machine names; or use --all")
    p.add_argument("--venue", required=True)
    p.add_argument("--all", action="store_true", help="every machine in the venue location")
    p.add_argument("--skip", default="", help="comma-separated names to exclude")
    p.add_argument("--keep", action="append", help="component name to preserve (repeatable)")
    p.add_argument("--keep-fragment", action="append", help="extra fragment id to preserve")
    p.add_argument("--force", action="store_true",
                   help="clear even when unrecognised components are present")
    p.add_argument("--restart", action="store_true",
                   help="restart each part afterwards so the motion service rebuilds its "
                        "frame system (required after dropping components)")
    p.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()

    venue = json.loads((HACK / "venues" / f"{args.venue}.json").read_text())
    if not venue["location"]["id"]:
        sys.exit("venue has no location id - run create-venue.py first")
    armmap, current = arm_fragment_map(venue)
    kid, key = load_creds()

    from viam.app.viam_client import ViamClient
    from viam.rpc.dial import DialOptions, Credentials
    c = await ViamClient.create_from_dial_options(DialOptions(
        auth_entity=kid, credentials=Credentials(type="api-key", payload=key)))
    try:
        app = c.app_client
        robots = await app.list_robots(location_id=venue["location"]["id"])
        parts = {}
        for r in robots:
            ps = await app.get_robot_parts(r.id)
            main = next((x for x in ps if getattr(x, "main_part", False)), ps[0] if ps else None)
            if main:
                parts[r.name] = main.id
        skip = {s.strip() for s in args.skip.split(",") if s.strip()}
        def numkey(s):
            m = re.search(r"(\d+)$", s)
            return (int(m.group(1)) if m else 10 ** 6, s)
        pool = sorted(parts, key=numkey) if args.all else args.machines
        names = [n for n in pool if n not in skip]
        if not names:
            sys.exit("no machines selected (give names or --all)")
        print(f"=== clear to fragment: {venue['event']} ===")
        print(f"  mode: {'APPLY' if args.apply else 'DRY RUN'}   machines: {len(names)}\n")
        if args.apply and not args.yes:
            print(f"  This REPLACES the config of {len(names)} machine(s): {', '.join(names)}")
            print("  Config history keeps the old version, but nothing else does.")
            try:
                if input("  Type CLEAR to continue: ").strip() != "CLEAR":
                    sys.exit("aborted")
            except (EOFError, KeyboardInterrupt):
                sys.exit("\naborted")
        done = []
        for n in names:
            if n not in parts:
                print(f"  {n:10s} not in this location")
                continue
            if await clear_one(app, n, parts[n], venue, armmap, current, args):
                done.append((n, parts[n]))
        if args.apply and args.restart and done:
            print("\n  restarting parts so the motion service rebuilds its frame system:")
            for n, pid in done:
                await app.mark_part_for_restart(pid)
                print(f"    {n:10s} restart requested")
        elif args.apply and done:
            print("\n  NOT restarted. Removing components leaves the motion service with a stale")
            print("  frame system - planned moves fail with 'Unlinked parts'. Run:")
            for n, pid in done:
                print(f"    viam machines part restart --part {pid}   # {n}")
    finally:
        c.close()


if __name__ == "__main__":
    asyncio.run(amain())
