"""LIVE test of clear-to-fragment.py against throwaway cloud machines.

Creates sacrificial machines, pushes hazard-shaped configs, runs the REAL script
via --apply, re-reads what landed, checks invariants, then deletes the machines.
No hardware, no arms: these machines have no compute box attached.

  python3 live_clear_test.py --location <id> [--keep]      # --keep = don't delete
"""
import argparse, asyncio, importlib.util, json, os, pathlib, subprocess, sys

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
SCR  = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ctf", SCR / "clear-to-fragment.py")
ctf  = importlib.util.module_from_spec(spec); spec.loader.exec_module(ctf)

VENUE_SRC = json.load(open(REPO / "hackathon" / "venues" / "oct-sf-hackathon.json"))
OLD850 = "fd2be28c-71e5-4b8a-90a8-a514dbe75ca7"
SF850  = "086f4921-8427-47b6-92e0-dbdc87f2267c"
OBST   = VENUE_SRC["obstacles_fragment"]["id"]
ORG    = VENUE_SRC["org"]["id"]

def armvars(ip="192.168.1.227", sn=""):
    return {"arm-name":"arm","arm-ip-address":ip,"cam-serial-number":sn,
            "base-axis-rotation-degrees":0,"translation":{"x":0,"y":0,"z":0}}

def frags(av, extra=None):
    f=[{"id":OLD850,"variables":av},
       {"id":OBST,"variables":{"table-translation":{"x":0,"y":0,"z":-123},
                               "ceiling-translation":{"x":0,"y":0,"z":1050}}}]
    if extra: f.append(extra)
    return f

VACUUM = {"api":"rdk:component:gripper","model":"viam:ufactory:vacuum_gripper",
          "name":"vacuum","frame":{"parent":"arm","translation":{"x":0,"y":0,"z":0}}}
UFACT  = {"type":"registry","module_id":"viam:ufactory","version":"0.5.0","name":"ufactory"}

# name -> (config, clear-args, invariant(written_cfg) -> (ok, detail))
def cases():
    def inv_vac(c):
        names=[x["name"] for x in c.get("components",[])]
        fm=c.get("fragment_mods",[])
        carried=any(m.get("$set",{}).get("components.gripper.disabled") is True
                    for e in fm for m in e.get("mods",[]))
        retgt=all(e.get("fragment_id")==SF850 for e in fm)
        ok = "vacuum" in names and carried and retgt
        return ok, f"components={names} mods={fm}"
    def inv_plain(c):
        names=sorted(x["name"] for x in c.get("components",[]))
        fid=[f["id"] for f in c.get("fragments",[])]
        ok = names==["wall-back","wall-left"] and SF850 in fid and OBST in fid
        return ok, f"components={names} fragments={[x[:8] for x in fid]}"
    def inv_carry(c):
        ok = all(k in c for k in ("agent","remotes","packages")) and \
             c["fragments"][0]["variables"].get("extra-var")=="keepme"
        return ok, f"top-level={sorted(c)} extra-var={c['fragments'][0]['variables'].get('extra-var')!r}"
    def inv_promote(c):
        v=c["fragments"][0]["variables"]
        ok = v.get("arm-ip-address")=="192.168.1.231" and v.get("cam-serial-number")=="215222077457"
        return ok, f"arm-ip={v.get('arm-ip-address')} cam-sn={v.get('cam-serial-number')!r}"

    return [
      ("ctftest-vacuum",
       {"fragments":frags(armvars(ip="192.168.1.214")),"components":[VACUUM],
        "modules":[UFACT],"services":[],
        "fragment_mods":[{"fragment_id":OLD850,
                          "mods":[{"$set":{"components.gripper.disabled":True}}]}]},
       ["--keep","vacuum"], inv_vac,
       "sfhack14 replica: vacuum kept AND gripper.disabled carried+retargeted"),

      ("ctftest-plain",
       {"fragments":frags(armvars()),
        "components":[{"api":"rdk:component:gripper","model":"erh:vmodutils:obstacle",
                       "name":"table-crop","attributes":{"geometries":[]}}],
        "modules":[],"services":[],"fragment_mods":[]},
       [], inv_plain,
       "ordinary cell: ends on SF fragment + 2 walls only"),

      ("ctftest-carry",
       {"fragments":frags(dict(armvars(), **{"extra-var":"keepme"})),
        "components":[],"modules":[],"services":[],"fragment_mods":[],
        "agent":{"version_control":{"agent":"stable"}},
        "remotes":[],"packages":[]},
       [], inv_carry,
       "agent/remotes/packages + unknown fragment variable survive"),

      ("ctftest-promote",
       {"fragments":frags(armvars(ip="192.168.1.1", sn="1")),
        "components":[],"modules":[],"services":[],
        "fragment_mods":[{"fragment_id":OLD850,
                          "mods":[{"$set":{"components.arm.attributes.host":"192.168.1.231",
                                           "components.cam.attributes.serial_number":"215222077457"}}]}]},
       [], inv_promote,
       "real hardware identity promoted out of mods before the wipe"),
    ]

async def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--location", help="existing location id; omit to create a throwaway one")
    ap.add_argument("--keep",action="store_true",
                    help="leave the machines and location in place for inspection")
    a=ap.parse_args()

    from viam.app.viam_client import ViamClient
    from viam.rpc.dial import DialOptions, Credentials
    kid,key = ctf.load_creds()
    client = await ViamClient.create_from_dial_options(DialOptions(
        auth_entity=kid, credentials=Credentials(type="api-key", payload=key)))
    app = client.app_client

    made_loc = None
    if not a.location:
        existing = {l.name: l.id for l in await app.list_locations(ORG)}
        if "ctf-test" in existing:
            a.location = existing["ctf-test"]
            print(f"reusing location ctf-test ({a.location})")
        else:
            loc = await app.create_location(org_id=ORG, name="ctf-test")
            a.location = loc.id; made_loc = loc.id
            print(f"created location ctf-test ({a.location})")

    # venue file pointing at the TEST location, so --all can never reach the event
    venue = json.loads(json.dumps(VENUE_SRC))
    venue["location"]["id"] = a.location
    vslug = "ctftest"
    vpath = REPO / "hackathon" / "venues" / f"{vslug}.json"
    vpath.write_text(json.dumps(venue, indent=2))

    made, results = [], []
    try:
        for name, cfg, flags, inv, desc in cases():
            rid = await app.new_robot(name=name, location_id=a.location)
            parts = await app.get_robot_parts(rid)
            pid = next(p.id for p in parts if p.main_part)
            await app.update_robot_part(robot_part_id=pid, name=parts[0].name, robot_config=cfg)
            made.append((name, rid, pid, flags, inv, desc))
            print(f"created {name:18s} robot={rid[:8]} part={pid[:8]}")

        print("\n" + "="*72)
        for name, rid, pid, flags, inv, desc in made:
            print(f"\n### {name}  --  {desc}")
            cmd=[sys.executable, str(SCR/"clear-to-fragment.py"), name,
                 "--venue", vslug, "--apply", "--yes", *flags]
            r=subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO))
            print("   " + "\n   ".join((r.stdout+r.stderr).strip().splitlines()[-12:]))
            part = await app.get_robot_part(pid)
            got = part.robot_config
            got = json.loads(got) if isinstance(got,str) else json.loads(json.dumps(got))
            ok, detail = inv(got)
            results.append((name, ok, detail))
            print(f"   VERIFY {'PASS' if ok else 'FAIL'}: {detail}")
    finally:
        if not a.keep:
            for name, rid, *_ in made:
                await app.delete_robot(robot_id=rid); print(f"deleted {name}")
            vpath.unlink(missing_ok=True)
            if made_loc:
                await app.delete_location(location_id=made_loc)
                print("deleted location ctf-test")
        client.close()

    print("\n" + "="*72)
    for n,ok,d in results: print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n{sum(1 for _,ok,_ in results if ok)}/{len(results)} live cases passed")
    return 0 if results and all(ok for _,ok,_ in results) else 1

sys.exit(asyncio.run(main()))
