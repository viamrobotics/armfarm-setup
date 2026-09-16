#!/usr/bin/env python3
"""Create an arm farm machine in Viam and get viam-agent running against it.

  1. create machine <name> in hackathons > Fine Motor Skills
  2. mint a machine-scoped API key
  3. get the agent running:
       fresh box      --install-agent   runs the official installer, which
                                        writes /etc/viam.json itself
       agent already  --write-config    writes /etc/viam.json and restarts
       neither flag                     writes the config next to your home dir
                                        and prints what to run
  4. set the part config: arm fragment + obstacles fragment, with this machine's values

Credentials: an org-scoped key in VIAM_API_KEY_ID / VIAM_API_KEY.
Dry run by default; --apply to write.

  provision-viam.py --name armfarm7 --cam-serial 243322071795 --wall left
  sudo -E provision-viam.py --name armfarm7 --cam-serial ... --wall left \
       --arm xarm850-gripper2 --apply --install-agent
"""
import argparse, asyncio, json, os, pathlib, shutil, subprocess, sys, tempfile, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
FLEET = json.loads((ROOT / "config" / "fleet.json").read_text())

INSTALLER_URL = "https://storage.googleapis.com/packages.viam.com/apps/viam-agent/install.sh"


def agent_installed() -> bool:
    if shutil.which("viam-agent") or pathlib.Path("/opt/viam/bin/viam-agent").exists():
        return True
    r = subprocess.run(["systemctl", "list-unit-files", "viam-agent.service"],
                       capture_output=True, text=True)
    return "viam-agent.service" in r.stdout


def install_agent(part_id: str, key_id: str, key: str) -> None:
    """Run Viam's official installer. It writes /etc/viam.json and sets up the service.

    Credentials go in the environment, not the command line, so they never appear in ps
    output. This is the vendor-documented install path - it does fetch and execute a
    remote script as root.
    """
    if os.geteuid() != 0:
        sys.exit("--install-agent needs root: re-run under `sudo -E`")
    with tempfile.NamedTemporaryFile("wb", suffix=".sh", delete=False) as fh:
        with urllib.request.urlopen(INSTALLER_URL, timeout=60) as resp:
            fh.write(resp.read())
        script = fh.name
    size = os.path.getsize(script)
    print(f"  downloaded installer ({size} bytes) from {INSTALLER_URL}")
    if size < 500:
        sys.exit("  installer looks truncated; aborting")
    env = {**os.environ, "VIAM_API_KEY_ID": key_id, "VIAM_API_KEY": key, "VIAM_PART_ID": part_id}
    subprocess.run(["/bin/sh", script], env=env, check=True)
    os.unlink(script)
    r = subprocess.run(["systemctl", "is-active", "viam-agent"], capture_output=True, text=True)
    print(f"  viam-agent: {r.stdout.strip()}")


def build_part_config(args):
    robot = FLEET["arm_configs"][args.arm]
    wd = FLEET["workcell_defaults"]
    side = wd["side-wall-left"] if args.wall == "left" else wd["side-wall-right"]
    return {"fragments": [
        {"id": robot["id"], "variables": {
            **FLEET["robot_fragment_vars"],
            "arm-ip-address": FLEET["arm"]["controller_ip"],
            "cam-serial-number": args.cam_serial}},
        {"id": FLEET["obstacles_fragment"]["id"], "variables": {
            "table-translation": wd["table-translation"],
            "front-wall-translation": wd["front-wall-translation"],
            "ceiling-translation": wd["ceiling-translation"],
            "side-wall-translation": side}},
    ]}


async def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--name", required=True)
    p.add_argument("--cam-serial", required=True,
                   help="DEVICE serial (not the ASIC serial sysfs reports)")
    p.add_argument("--wall", required=True, choices=["left", "right"])
    p.add_argument("--arm", default="xarm6-gripper2",
                   choices=["xarm6-original", "xarm6-gripper2",
                            "xarm850-original", "xarm850-gripper2"])
    p.add_argument("--apply", action="store_true")
    p.add_argument("--install-agent", action="store_true",
                   help="fresh box: run Viam's installer (needs sudo -E)")
    p.add_argument("--write-config", action="store_true",
                   help="agent already installed: write /etc/viam.json (needs sudo)")
    args = p.parse_args()

    cfg = build_part_config(args)
    have_agent = agent_installed()
    meta = FLEET["arm_configs"][args.arm]
    print(f"=== {args.name} ===")
    print(f"  org/location : {FLEET['org']['name']} / {FLEET['location']['name']}")
    print(f"  arm config   : {args.arm} -> {meta['name']}")
    print(f"  note         : {meta['note']}")
    print(f"  cam serial   : {args.cam_serial}")
    print(f"  wall side    : {args.wall} -> {cfg['fragments'][1]['variables']['side-wall-translation']}")
    print(f"  viam-agent   : {'already installed' if have_agent else 'NOT installed'}")
    if not (args.install_agent or args.write_config):
        print(f"  -> suggest {'--write-config' if have_agent else '--install-agent'}")
    if args.install_agent and have_agent:
        print("  NOTE: agent already present; the installer will reconfigure it")

    if not args.apply:
        print("\n[dry run] part config that would be written:")
        print(json.dumps(cfg, indent=2))
        return

    from viam.app.viam_client import ViamClient
    from viam.app.app_client import APIKeyAuthorization
    from viam.rpc.dial import DialOptions, Credentials

    kid, key = os.environ.get("VIAM_API_KEY_ID"), os.environ.get("VIAM_API_KEY")
    if not (kid and key):
        sys.exit("set VIAM_API_KEY_ID / VIAM_API_KEY (org-scoped). Under sudo, use `sudo -E`.")

    client = await ViamClient.create_from_dial_options(DialOptions(
        auth_entity=kid, credentials=Credentials(type="api-key", payload=key)))
    try:
        app = client.app_client
        robot_id = await app.new_robot(name=args.name, location_id=FLEET["location"]["id"])
        print(f"\n  created machine: {robot_id}")

        part = (await app.get_robot_parts(robot_id))[0]
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
        print("  part config applied (arm + obstacles fragments)")

        if args.install_agent:
            print("  installing viam-agent…")
            install_agent(part_id, new_kid, new_key)
            return

        viam_json = {"cloud": {
            "app_address": "https://app.viam.com:443",
            "id": part_id,
            "api_key": {"id": new_kid, "key": new_key}}}
        if args.write_config:
            if os.geteuid() != 0:
                sys.exit("--write-config needs root: re-run under `sudo -E`")
            pathlib.Path("/etc/viam.json").write_text(json.dumps(viam_json, indent=2) + "\n")
            os.chmod("/etc/viam.json", 0o644)
            subprocess.run(["systemctl", "restart", "viam-agent"], check=True)
            print("  wrote /etc/viam.json and restarted viam-agent")
        else:
            out = pathlib.Path.home() / f"viam.json.{args.name}"
            out.write_text(json.dumps(viam_json, indent=2) + "\n")
            out.chmod(0o600)
            print(f"  wrote {out}")
            print(f"  -> sudo install -m644 {out} /etc/viam.json && "
                  f"sudo systemctl restart viam-agent")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
