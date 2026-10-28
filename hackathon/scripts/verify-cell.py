#!/usr/bin/env python3
"""Is this station working, and is the mounting right? Runs on the LAPTOP.

  verify-cell.py --venue oct-sf-hackathon --name sfhack3
  verify-cell.py --venue oct-sf-hackathon --name sfhack3 --image   # also save a camera frame

Connects to the machine through the cloud with the key provision-viam.py minted, then:

  1. resource states: everything should be READY. The arm is last to come up, and
     "Emergency Stop Button Pushed In" is the physical button on the controller, not
     a config problem.
  2. arm pose -> where the camera would sit under each mounting (the HQ
     identify-config.py math), and the exact question to ask the person at the arm.
     The two candidates are ~168mm apart; position is eyeball-able, orientation is not.
     Never decide mounting from how the gripper looks: it sits on the tool axis and
     does not move between the two mountings.
  3. optionally a camera frame, which proves the RealSense end to end.
"""
import argparse, asyncio, importlib.util, json, math, os, pathlib, sys

HACK = pathlib.Path(__file__).resolve().parent.parent
ROOT = HACK.parent
OUT = HACK / "out"


def load_identify():
    spec = importlib.util.spec_from_file_location("identify_config", ROOT / "scripts" / "identify-config.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--venue", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--image", action="store_true", help="save a frame from cam to hackathon/out/")
    p.add_argument("--timeout", type=float, default=20.0)
    args = p.parse_args()

    venue = json.loads((HACK / "venues" / f"{args.venue}.json").read_text())
    cred_file = OUT / f"viam.json.{args.name}"
    if not cred_file.exists():
        sys.exit(f"no {cred_file}; run provision-viam.py --apply for {args.name} first")
    cloud = json.loads(cred_file.read_text())["cloud"]
    part_id, kid, key = cloud["id"], cloud["api_key"]["id"], cloud["api_key"]["key"]
    address = f"{args.name}-main.{venue['location']['id']}.viam.cloud"

    from viam.robot.client import RobotClient
    from viam.components.arm import Arm
    from viam.components.camera import Camera
    from viam.proto.robot import GetMachineStatusResponse, ResourceStatus

    print(f"=== {args.name} ({address}) ===")
    opts = RobotClient.Options.with_api_key(api_key=key, api_key_id=kid)
    opts.dial_options.timeout = args.timeout
    try:
        robot = await RobotClient.at_address(address, opts)
    except Exception as e:
        sys.exit(f"could not connect: {e}\n  Is viam-agent running on the box, and online in https://app.viam.com ?")

    try:
        # ---------------------------------------------------------- 1. states
        st = await robot.get_machine_status()
        print(f"  machine state: {GetMachineStatusResponse.State.Name(st.state)}")
        bad = 0
        for r in st.resources:
            nm = r.name.name
            state = ResourceStatus.State.Name(r.state)
            if nm in ("arm", "cam", "gripper", "table", "ceiling") or "READY" not in state:
                line = f"    {nm:12s} {state}"
                if getattr(r, "error", None):
                    line += f"  {str(r.error)[:120]}"
                print(line)
            if "READY" not in state and nm in ("arm", "cam", "gripper"):
                bad += 1
        names = {r.name.name for r in st.resources}
        for want in ("arm", "cam", "gripper", "table", "ceiling"):
            if want not in names:
                print(f"    {want:12s} MISSING from the config"); bad += 1

        # ---------------------------------------------------------- 2. mounting
        if "arm" in names:
            try:
                arm = Arm.from_robot(robot, "arm")
                pose = await arm.get_end_position()
                x, y, z = pose.x, pose.y, pose.z
                ox, oy, oz, th = pose.o_x, pose.o_y, pose.o_z, pose.theta
                print(f"\n  arm pose: ({x:.1f}, {y:.1f}, {z:.1f})  ov ({ox:.4f}, {oy:.4f}, {oz:.4f})  th {th:.2f}")
                ic = load_identify()
                R = ic.ov_to_R(ox, oy, oz, th)
                pos = {}
                for mount, t in ic.CAM_T.items():
                    off = ic._mv(R, list(t))
                    pos[mount] = [x + off[0], y + off[1], z + off[2]]
                a_, b_ = pos["original"], pos["gripper2"]
                d_orig, d_grip = math.dist((0, 0, 0), a_), math.dist((0, 0, 0), b_)
                print("  camera would be at (world mm):")
                for m, q in pos.items():
                    print(f"    {m:9s} ({q[0]:7.0f}, {q[1]:7.0f}, {q[2]:7.0f})  {math.dist((0,0,0), q):.0f} mm from base")
                print(f"  candidates are {math.dist(a_, b_):.0f} mm apart")
                print("\n  ASK THE PERSON AT THE ARM:")
                print("    Looking at the wrist, is the camera nearer the arm's base, or further from it?")
                further = "gripper2" if d_grip > d_orig else "original"
                nearer = "original" if further == "gripper2" else "gripper2"
                print(f"      further from base -> {further}")
                print(f"      nearer the base   -> {nearer}")
                print("  If the answer disagrees with the fragment, swap it:")
                print(f"    provision-viam.py --venue {args.venue} --name {args.name} --arm <model>-<mounting> "
                      f"--arm-ip <ip> --apply --config-only")
            except Exception as e:
                print(f"\n  arm pose unavailable: {str(e)[:160]}")
                bad += 1

        # ---------------------------------------------------------- 3. camera
        if args.image and "cam" in names:
            try:
                cam = Camera.from_robot(robot, "cam")
                images, _ = await cam.get_images(filter_source_names=["color"])
                if not images:
                    images, _ = await cam.get_images()
                if not images:
                    raise RuntimeError("camera returned no images")
                img = images[0]
                mime = str(img.mime_type)
                ext = "png" if "png" in mime else "jpg" if "jpeg" in mime or "jpg" in mime else "bin"
                OUT.mkdir(exist_ok=True)
                out = OUT / f"{args.name}-cam.{ext}"
                out.write_bytes(img.data)
                print(f"\n  camera frame saved: {out} ({len(img.data)} bytes, {mime}, source {img.name!r})")
            except Exception as e:
                print(f"\n  camera frame failed: {str(e)[:160]}")
                bad += 1
    finally:
        await robot.close()

    print("\n" + ("OK" if not bad else f"{bad} problem(s) - see above"))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    asyncio.run(main())
