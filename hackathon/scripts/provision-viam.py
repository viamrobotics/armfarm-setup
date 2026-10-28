#!/usr/bin/env python3
"""Create this station's machine in the event location and produce its viam.json.

Runs on the LAPTOP. Nothing here touches the box; copy the output over and run
hackathon/scripts/install-config.sh there.

  1. find or create machine <name> in the venue's location
  2. set the part config: arm fragment (model + mounting) + the venue obstacles
     fragment (table + ceiling), with this station's values
  3. mint a machine-scoped key and write hackathon/out/viam.json.<name>

  provision-viam.py --venue oct-sf-hackathon --name sfhack3 --arm xarm6-gripper2 \\
      --arm-ip 192.168.1.233                    # dry run: prints the config
  ... --apply                                   # do it
  ... --apply --config-only                     # fragment swap only: no key, no viam.json

Every box gets a fresh machine in the event location, including boxes borrowed from
the HQ arm farm: their old machine stays in Arm Farm untouched, and install-config.sh
backs up the old /etc/viam.json so they can go back.

Credentials: org-scoped key in VIAM_API_KEY_ID / VIAM_API_KEY, or orgkey.txt at the
repo root (bootstrap-laptop.sh writes it).
"""
import argparse, asyncio, ipaddress, json, os, pathlib, sys

HACK = pathlib.Path(__file__).resolve().parent.parent
ROOT = HACK.parent
# Arm fragments (model x mounting) are shared with the HQ flow; their ids live in the
# HQ fleet.json and are read here, never written.
FLEET = json.loads((ROOT / "config" / "fleet.json").read_text())
OUT = HACK / "out"


def load_env_file():
    f = ROOT / "orgkey.txt"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def load_venue(slug):
    p = HACK / "venues" / f"{slug}.json"
    if not p.exists():
        sys.exit(f"no venue file {p}. Available: "
                 + ", ".join(x.stem for x in (HACK / "venues").glob("*.json")))
    v = json.loads(p.read_text())
    if not v["location"]["id"] or not v["obstacles_fragment"]["id"]:
        sys.exit(f"{p.name} has no location/fragment id yet. Run first:\n"
                 f"  {HACK}/venv/bin/python {HACK}/scripts/create-venue.py --venue {slug} --apply")
    return v


def build_part_config(venue, args):
    robot = FLEET["arm_configs"][args.arm]
    table = dict(venue["workcell"]["table-translation"])
    ceiling = dict(venue["workcell"]["ceiling-translation"])
    if args.table_z is not None:
        table["z"] = args.table_z
    if args.ceiling_z is not None:
        ceiling["z"] = args.ceiling_z
    return {"fragments": [
        {"id": robot["id"], "variables": {
            **venue["robot_fragment_vars"],
            "arm-ip-address": args.arm_ip,
            "cam-serial-number": args.cam_serial}},
        {"id": venue["obstacles_fragment"]["id"], "variables": {
            "table-translation": table,
            "ceiling-translation": ceiling}},
    ]}


async def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--venue", required=True, help="slug of hackathon/venues/<slug>.json")
    p.add_argument("--name", required=True, help="machine + hostname, e.g. sfhack3")
    p.add_argument("--arm", required=True,
                   choices=sorted(FLEET["arm_configs"].keys()),
                   help="model x mounting. Model from the controller label; mounting "
                        "verified later by verify-cell.py (gripper2 is the expected default)")
    p.add_argument("--arm-ip", required=True,
                   help="this arm controller's address (find-arm-ip.sh on the box)")
    p.add_argument("--cam-serial", default=None,
                   help="RealSense DEVICE serial. Default: empty, which makes the module "
                        "use the one camera on the box")
    p.add_argument("--table-z", type=float, default=None,
                   help="table box CENTER z in mm if this station's table differs "
                        "(top face = center + 100)")
    p.add_argument("--ceiling-z", type=float, default=None,
                   help="ceiling box CENTER z in mm (underside = center - 50)")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--config-only", action="store_true",
                   help="update the part config and stop: no key minted, no viam.json")
    args = p.parse_args()

    load_env_file()
    venue = load_venue(args.venue)
    if args.cam_serial is None:
        args.cam_serial = venue.get("camera", {}).get("serial", "")

    prefix = venue["machine_prefix"]
    if not args.name.startswith(prefix):
        print(f"  note: name {args.name!r} does not start with the venue prefix {prefix!r}")

    host_if = ipaddress.ip_interface(venue["network"]["arm_host_ip_cidr"])
    try:
        arm_ip = ipaddress.ip_address(args.arm_ip)
    except ValueError:
        p.error(f"--arm-ip {args.arm_ip!r} is not a valid IP address")
    if arm_ip not in host_if.network:
        p.error(f"--arm-ip {arm_ip} is not on the arm subnet {host_if.network}")
    if arm_ip == host_if.ip:
        p.error(f"--arm-ip {arm_ip} collides with the host address {host_if.ip}")

    cfg = build_part_config(venue, args)
    meta = FLEET["arm_configs"][args.arm]
    print(f"=== {args.name} @ {venue['event']} ===")
    print(f"  org/location : {venue['org']['name']} / {venue['location']['name']} ({venue['location']['id']})")
    print(f"  arm config   : {args.arm} -> {meta['name']} ({meta['model']})")
    print(f"  arm ip       : {args.arm_ip}")
    print(f"  cam serial   : {args.cam_serial!r} {'(first camera found)' if not args.cam_serial else ''}")
    print(f"  obstacles    : {venue['obstacles_fragment']['name']}  "
          f"table {cfg['fragments'][1]['variables']['table-translation']}  "
          f"ceiling {cfg['fragments'][1]['variables']['ceiling-translation']}")
    if not args.apply:
        print("\n[dry run] part config that would be written:")
        print(json.dumps(cfg, indent=2))
        return

    kid, key = os.environ.get("VIAM_API_KEY_ID"), os.environ.get("VIAM_API_KEY")
    if not (kid and key):
        sys.exit("set VIAM_API_KEY_ID / VIAM_API_KEY, or run hackathon/scripts/bootstrap-laptop.sh")

    from viam.app.viam_client import ViamClient
    from viam.app.app_client import APIKeyAuthorization
    from viam.rpc.dial import DialOptions, Credentials

    client = await ViamClient.create_from_dial_options(DialOptions(
        auth_entity=kid, credentials=Credentials(type="api-key", payload=key)))
    try:
        app = client.app_client
        loc_id = venue["location"]["id"]
        existing = [r for r in await app.list_robots(location_id=loc_id) if r.name == args.name]
        if existing:
            robot_id = existing[0].id
            print(f"\n  reusing machine: {args.name} ({robot_id})")
        else:
            robot_id = await app.new_robot(name=args.name, location_id=loc_id)
            print(f"\n  created machine: {robot_id}")

        part = (await app.get_robot_parts(robot_id))[0]
        part_id = part.id
        print(f"  main part: {part.name} ({part_id})")
        print(f"  address  : {part.fqdn}")

        await app.update_robot_part(robot_part_id=part_id, name=part.name, robot_config=cfg)
        print("  part config applied (arm + obstacles fragments)")

        if args.config_only:
            print("  --config-only: no key minted, no viam.json written")
            return

        new_key, new_kid = await app.create_key(
            org_id=venue["org"]["id"],
            authorizations=[APIKeyAuthorization(
                role="owner", resource_type="robot", resource_id=robot_id)],
            name=f"{args.name}-agent")
        print(f"  minted machine key: {new_kid[:8]}... (value hidden)")

        viam_json = {"cloud": {
            "app_address": "https://app.viam.com:443",
            "id": part_id,
            "api_key": {"id": new_kid, "key": new_key}}}
        OUT.mkdir(exist_ok=True)
        out = OUT / f"viam.json.{args.name}"
        out.write_text(json.dumps(viam_json, indent=2) + "\n")
        out.chmod(0o600)
        print(f"  wrote {out}")
        print()
        print("Next (the box's password is typed by you, once):")
        print(f"  scp {out} viam@{args.name}.local:~/")
        print(f"  ssh -t viam@{args.name}.local sudo bash ~/armfarm-setup/hackathon/scripts/install-config.sh ~/viam.json.{args.name}")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
