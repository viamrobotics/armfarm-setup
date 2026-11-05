"""Offline tests for clear-to-fragment.py - no network, no machines.

  hackathon/venv/bin/python hackathon/scripts/test_clear_to_fragment.py

Covers the cases that make this script dangerous: a kept hardware component really
is kept, an unrecognised fragment_mod refuses, top-level config keys survive, unknown
fragment variables survive, a bad arm IP refuses, and mod-promotion still works.
"""
import asyncio, importlib.util, json, sys, types
HERE = __import__("pathlib").Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ctf", HERE / "clear-to-fragment.py")
ctf = importlib.util.module_from_spec(spec); spec.loader.exec_module(ctf)

VENUE = json.load(open(HERE.parent / "venues" / "oct-sf-hackathon.json"))
ARMMAP, CURRENT = ctf.arm_fragment_map(VENUE)
SF850 = "086f4921-8427-47b6-92e0-dbdc87f2267c"
OLD850 = "fd2be28c-71e5-4b8a-90a8-a514dbe75ca7"
OBST = VENUE["obstacles_fragment"]["id"]

class Part:
    def __init__(self, cfg): self.robot_config, self.name = cfg, "m-main"
class App:
    def __init__(self, cfg): self.part = Part(cfg); self.written = None
    async def get_robot_part(self, pid): return self.part
    async def update_robot_part(self, robot_part_id, name, robot_config): self.written = robot_config
    async def mark_part_for_restart(self, pid): pass

def args(**kw):
    d = dict(keep=None, keep_fragment=None, force=False, apply=True, restart=False, yes=True)
    d.update(kw); return types.SimpleNamespace(**d)

def base(**over):
    cfg = {"fragments":[{"id":OLD850,"variables":{"arm-name":"arm","arm-ip-address":"192.168.1.227",
                         "cam-serial-number":"","base-axis-rotation-degrees":0,
                         "translation":{"x":0,"y":0,"z":0},"extra-var":"keepme"}},
                        {"id":OBST,"variables":{"table-translation":{"x":0,"y":0,"z":-123},
                                                "ceiling-translation":{"x":0,"y":0,"z":1050}}}],
           "components":[], "modules":[], "services":[], "fragment_mods":[]}
    cfg.update(over); return cfg

async def run(cfg, a):
    app = App(cfg)
    ok = await ctf.clear_one(app, "m", "pid", VENUE, ARMMAP, CURRENT, a)
    return ok, app.written

async def main():
    print("=== T1: venue file parses (B1) ===")
    print(f"   arm_fragment_map -> {len(ARMMAP)} fragment ids, models {sorted(CURRENT)}")

    print("\n=== T2: vacuum cell with --keep vacuum (B2) ===")
    vac = {"api":"rdk:component:gripper","model":"viam:ufactory:vacuum_gripper","name":"vacuum",
           "frame":{"parent":"arm","translation":{"x":0,"y":0,"z":0}}}
    ok,w = await run(base(components=[vac]), args(keep=["vacuum"]))
    names = [c["name"] for c in (w or {}).get("components",[])]
    print(f"   applied={ok}  components written = {names}")
    print(f"   {'PASS' if 'vacuum' in names else 'FAIL'}: vacuum preserved")

    print("\n=== T3: hardware mod (gripper.disabled) must REFUSE (B3) ===")
    mods=[{"fragment_id":OLD850,"mods":[{"$set":{"components.gripper.disabled":True}}]}]
    ok,w = await run(base(fragment_mods=mods), args())
    print(f"   {'PASS' if not ok and w is None else 'FAIL'}: refused={not ok}, nothing written={w is None}")

    print("\n=== T4: top-level keys carried (S1) ===")
    ok,w = await run(base(agent={"version":"x"}, remotes=[{"name":"r"}], packages=[{"n":1}]), args())
    print(f"   written top-level: {sorted((w or {}).keys())}")
    print(f"   {'PASS' if all(k in (w or {}) for k in ('agent','remotes','packages')) else 'FAIL'}: agent/remotes/packages carried")

    print("\n=== T5: unknown variables carried (S3) ===")
    print(f"   {'PASS' if (w or {})['fragments'][0]['variables'].get('extra-var')=='keepme' else 'FAIL'}: extra-var survived")

    print("\n=== T6: bad arm ip REFUSED (S2) ===")
    for ip,label in (("192.168.1.1","gateway"),("","empty"),("192.168.1.150","host"),("10.0.0.5","off-subnet")):
        c = base(); c["fragments"][0]["variables"]["arm-ip-address"]=ip
        ok,w = await run(c, args())
        print(f"   {ip or '<empty>':16s} ({label:10s}) refused={not ok}  {'PASS' if not ok else 'FAIL'}")

    print("\n=== T7: promotion from mods still works ===")
    mods=[{"fragment_id":OLD850,"mods":[{"$set":{"components.arm.attributes.host":"192.168.1.231",
                                                 "components.cam.attributes.serial_number":"SN123"}}]}]
    c = base(fragment_mods=mods); c["fragments"][0]["variables"]["arm-ip-address"]="192.168.1.1"
    ok,w = await run(c, args())
    v = (w or {}).get("fragments",[{}])[0].get("variables",{})
    print(f"   applied={ok} arm-ip={v.get('arm-ip-address')} cam-sn={v.get('cam-serial-number')!r}")
    print(f"   {'PASS' if v.get('arm-ip-address')=='192.168.1.231' and v.get('cam-serial-number')=='SN123' else 'FAIL'}")

    print("\n=== T8: normal cell -> correct fragment + walls ===")
    ok,w = await run(base(), args())
    print(f"   fragments: {[f['id'][:8] for f in w['fragments']]}  (want {SF850[:8]}, {OBST[:8]})")
    print(f"   components: {[c['name'] for c in w['components']]}")
    print(f"   mods/modules/services: {len(w['fragment_mods'])}/{len(w['modules'])}/{len(w['services'])}")
    print(f"   {'PASS' if w['fragments'][0]['id']==SF850 and len(w['components'])==2 else 'FAIL'}")
asyncio.run(main())
