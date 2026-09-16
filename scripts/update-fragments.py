#!/usr/bin/env python3
"""Push a local JSON config into a Viam fragment via the app API.

The app API can write fragments even though the MCP tools and the `viam fragment`
CLI are read-only. Credentials: org-scoped key in VIAM_API_KEY_ID / VIAM_API_KEY.

  update-fragments.py <fragment-id> <config.json>            # dry run
  update-fragments.py <fragment-id> <config.json> --apply
"""
import asyncio, json, os, sys


def folders(cfg):
    return {c.get("name"): (c.get("ui_folder") or {}).get("name")
            for c in (cfg or {}).get("components", [])}


async def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        sys.exit(__doc__)
    fid, path = args
    apply_ = "--apply" in sys.argv

    from viam.app.viam_client import ViamClient
    from viam.rpc.dial import DialOptions, Credentials

    kid, key = os.environ.get("VIAM_API_KEY_ID"), os.environ.get("VIAM_API_KEY")
    if not (kid and key):
        sys.exit("set VIAM_API_KEY_ID / VIAM_API_KEY (org-scoped)")

    new = json.load(open(path))
    client = await ViamClient.create_from_dial_options(DialOptions(
        auth_entity=kid, credentials=Credentials(type="api-key", payload=key)))
    try:
        cloud = client.app_client
        cur = await cloud.get_fragment(fragment_id=fid)
        print(f"=== {cur.name} ({fid[:8]}…) visibility={cur.visibility} ===")
        print(f"  components {len(new.get('components', []))}  modules {len(new.get('modules', []))}")
        print(f"  folders now: {folders(cur.fragment)}")
        print(f"  folders new: {folders(new)}")
        if not apply_:
            print("  [dry run] not writing")
            return
        # name is required; passing the existing one avoids a rename.
        # visibility omitted => unchanged.
        await cloud.update_fragment(fragment_id=fid, name=cur.name, config=new)
        back = await cloud.get_fragment(fragment_id=fid)
        print(f"  WROTE -> folders now: {folders(back.fragment)}")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
