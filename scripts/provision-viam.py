#!/usr/bin/env python3
"""Create an arm farm machine in Viam and apply its config.

  1. create machine <name> in hackathons > Fine Motor Skills
  2. mint a machine-scoped API key
  3. write /etc/viam.json   (needs sudo; use --write-config, else it prints the file)
  4. set the part config: robot fragment + obstacles fragment, with this machine's values

Credentials: an org-scoped key in VIAM_API_KEY_ID / VIAM_API_KEY.
Dry run by default; --apply to write.

  provision-viam.py --name armfarm7 --cam-serial 243322071795 --wall left
  provision-viam.py --name armfarm7 --cam-serial ... --wall right --arm xarm850 --apply
"""
import argparse, asyncio, json, os, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
FLEET = json.loads((ROOT / "config" / "fleet.json").read_text())


def build_part_config(args):
    f = FLEET["fragments"]
    robot = f[args.arm]
    if not robot["id"]:
        sys.exit(f"no fragment id for {args.arm}: {robot['name']}")
    wd = FLEET["workcell_defaults"]
    side = wd["side-wall-left"] if args.wall == "left" else wd["side-wall-right"]
    return {
        "fragments": [
            {
                "id": robot["id"],
                "variables": {
                    **FLEET["robot_fragment_vars"],
                    "arm-ip-address": FLEET["arm"]["controller_ip"],
                    "cam-serial-number": args.cam_serial,
                },
            },
            {
                "id": f["obstacles"]["id"],
                "variables": {
                    "table-translation": wd["table-translation"],
                    "front-wall-translation": wd["front-wall-translation"],
                    "ceiling-translation": wd["ceiling-translation"],
                    "side-wall-translation": side,
                },
            },
        ]
    }


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--cam-serial", required=True,
                   help="DEVICE serial (not the ASIC serial sysfs reports)")
    p.add_argument("--wall", required=True, choices=["left", "right"])
    p.add_argument("--arm", default="xarm6", choices=["xarm6", "xarm850"])
    p.add_argument("--apply", action="store_true")
    p.add_argument("--write-config", action="store_true",
                   help="also write /etc/viam.json (run under sudo)")
    args = p.parse_args()

    cfg = build_part_config(args)
    print(f"=== {args.name} ===")
    print(f"  org/location : {FLEET['org']['name']} / {FLEET['location']['name']}")
    print(f"  arm model    : {args.arm} -> {FLEET['fragments'][args.arm]['name']}")
    print(f"  cam serial   : {args.cam_serial}")
    print(f"  wall side    : {args.wall} -> {cfg['fragments'][1]['variables']['side-wall-translation']}")
    if not args.apply:
        print("\n[dry run] part config that would be written:")
        print(json.dumps(cfg, indent=2))
        return

    from viam.app.viam_client import ViamClient
    from viam.app.app_client import APIKeyAuthorization
    from viam.rpc.dial import DialOptions, Credentials

    kid, key = os.environ.get("VIAM_API_KEY_ID"), os.environ.get("VIAM_API_KEY")
    if not (kid and key):
        sys.exit("set VIAM_API_KEY_ID / VIAM_API_KEY (org-scoped)")

    client = await ViamClient.create_from_dial_options(DialOptions(
        auth_entity=kid, credentials=Credentials(type="api-key", payload=key)))
    try:
        app = client.app_client
        robot_id = await app.new_robot(name=args.name, location_id=FLEET["location"]["id"])
        print(f"  created machine: {robot_id}")

        parts = await app.get_robot_parts(robot_id)
        part = parts[0]
        part_id = part.proto.id
        print(f"  main part: {part_id}")

        new_kid, new_key = await app.create_key(
            org_id=FLEET["org"]["id"],
            authorizations=[APIKeyAuthorization(
                role="owner", resource_type="robot", resource_id=robot_id)],
            name=f"{args.name}-agent")
        print(f"  minted machine key: {new_kid[:8]}… (value hidden)")

        await app.update_robot_part(robot_part_id=part_id, name=part.proto.name,
                                    robot_config=cfg)
        print("  part config applied (both fragments)")

        viam_json = {"cloud": {
            "app_address": "https://app.viam.com:443",
            "id": part_id,
            "api_key": {"id": new_kid, "key": new_key},
        }}
        if args.write_config:
            pathlib.Path("/etc/viam.json").write_text(json.dumps(viam_json, indent=2) + "\n")
            os.chmod("/etc/viam.json", 0o644)
            print("  wrote /etc/viam.json - now: systemctl restart viam-agent")
        else:
            out = pathlib.Path.home() / f"viam.json.{args.name}"
            out.write_text(json.dumps(viam_json, indent=2) + "\n")
            out.chmod(0o600)
            print(f"  wrote {out}  ->  sudo install -m644 {out} /etc/viam.json")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
