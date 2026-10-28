#!/usr/bin/env python3
"""One-time setup for an event: the Viam location and the obstacles fragment.

Reads hackathon/venues/<slug>.json. For each of location and obstacles_fragment, if
the id is empty it creates the thing and writes the id back into the venue file.
If the id is present it just confirms the thing exists. Safe to re-run.

  create-venue.py --venue oct-sf-hackathon             # dry run: says what it would do
  create-venue.py --venue oct-sf-hackathon --apply
  create-venue.py --venue oct-sf-hackathon --apply --push-fragment   # also push the local
                                                                      # fragment JSON to the cloud

Credentials: org-scoped key in VIAM_API_KEY_ID / VIAM_API_KEY, or orgkey.txt at the
repo root (bootstrap-laptop.sh writes it).
"""
import argparse, asyncio, json, os, pathlib, sys

HACK = pathlib.Path(__file__).resolve().parent.parent
ROOT = HACK.parent


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
    return p, json.loads(p.read_text())


async def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--venue", required=True, help="slug of hackathon/venues/<slug>.json")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--push-fragment", action="store_true",
                    help="write hackathon/fragments/<name>.json into the cloud fragment "
                         "(create it if missing). Without this, an existing fragment is "
                         "left as-is.")
    args = ap.parse_args()

    load_env_file()
    venue_path, venue = load_venue(args.venue)
    frag_src = HACK / "fragments" / f"{venue['obstacles_fragment']['name']}.json"
    frag_cfg = json.loads(frag_src.read_text())

    print(f"=== {venue['event']} ({args.venue}) ===")
    print(f"  org       : {venue['org']['name']} ({venue['org']['id']})")
    print(f"  location  : {venue['location']['name']!r} id={venue['location']['id'] or '(none yet)'}")
    print(f"  obstacles : {venue['obstacles_fragment']['name']!r} id={venue['obstacles_fragment']['id'] or '(none yet)'}")
    print(f"              source {frag_src.relative_to(ROOT)}: "
          f"{[c['name'] for c in frag_cfg['components']]}")

    plan = []
    if not venue["location"]["id"]:
        plan.append("create location")
    if not venue["obstacles_fragment"]["id"]:
        plan.append("create obstacles fragment from local JSON")
    elif args.push_fragment:
        plan.append("push local JSON into the existing obstacles fragment")
    print(f"  plan      : {', '.join(plan) if plan else 'nothing to do'}")
    if not args.apply:
        print("\n[dry run] re-run with --apply")
        return

    kid, key = os.environ.get("VIAM_API_KEY_ID"), os.environ.get("VIAM_API_KEY")
    if not (kid and key):
        sys.exit("set VIAM_API_KEY_ID / VIAM_API_KEY, or run hackathon/scripts/bootstrap-laptop.sh")

    from viam.app.viam_client import ViamClient
    from viam.rpc.dial import DialOptions, Credentials
    client = await ViamClient.create_from_dial_options(DialOptions(
        auth_entity=kid, credentials=Credentials(type="api-key", payload=key)))
    changed = False
    try:
        app = client.app_client
        org_id = venue["org"]["id"]

        # ---------------------------------------------------------- location
        if venue["location"]["id"]:
            loc = await app.get_location(location_id=venue["location"]["id"])
            print(f"\n  location exists: {loc.name} ({loc.id})")
        else:
            # Reuse by name if someone already made it in the UI.
            existing = [l for l in await app.list_locations(org_id=org_id)
                        if l.name == venue["location"]["name"]]
            if existing:
                loc = existing[0]
                print(f"\n  location already exists by name, reusing: {loc.id}")
            else:
                loc = await app.create_location(org_id=org_id, name=venue["location"]["name"])
                print(f"\n  created location: {loc.name} ({loc.id})")
            venue["location"]["id"] = loc.id
            changed = True

        # ---------------------------------------------------------- fragment
        fname = venue["obstacles_fragment"]["name"]
        if venue["obstacles_fragment"]["id"]:
            fr = await app.get_fragment(fragment_id=venue["obstacles_fragment"]["id"])
            print(f"  fragment exists: {fr.name} ({fr.id})")
            if args.push_fragment:
                await app.update_fragment(fragment_id=fr.id, name=fr.name, config=frag_cfg)
                print(f"  pushed {frag_src.name} into {fr.name}")
        else:
            existing = [f for f in await app.list_fragments(org_id=org_id, show_public=False)
                        if f.name == fname]
            if existing:
                fr = existing[0]
                print(f"  fragment already exists by name, reusing: {fr.id}")
                if args.push_fragment:
                    await app.update_fragment(fragment_id=fr.id, name=fr.name, config=frag_cfg)
                    print(f"  pushed {frag_src.name} into {fr.name}")
            else:
                fr = await app.create_fragment(org_id=org_id, name=fname, config=frag_cfg)
                print(f"  created fragment: {fr.name} ({fr.id})")
            venue["obstacles_fragment"]["id"] = fr.id
            changed = True
    finally:
        client.close()

    if changed:
        venue_path.write_text(json.dumps(venue, indent=2) + "\n")
        print(f"\n  wrote ids back to {venue_path.relative_to(ROOT)} - commit it so the team gets them")
    print("\nOK")


if __name__ == "__main__":
    asyncio.run(main())
