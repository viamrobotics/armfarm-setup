#!/usr/bin/env python3
"""One-time setup for an event: the Viam location and the obstacles fragment.

Reads hackathon/venues/<slug>.json. For each of location and obstacles_fragment, if
the id is empty it looks the thing up BY NAME and reuses it if it is already there
(someone may have made it in the UI, or on another laptop), creating it only when
nothing matches, then writes the id back into the venue file. If the id is present
it just confirms the thing exists. Safe to re-run.

The dry run connects too - read-only - so that it reports "reuse" rather than
guessing "create" from an empty id. It therefore needs credentials as well.

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

        # ------------------------------------------------- what is already there
        # Resolved before the plan is printed, and before --apply branches, so the
        # dry run and the apply cannot disagree. An empty id in the venue file does
        # NOT mean "does not exist" - someone may have made it in the UI, or on
        # another operator's laptop. Always look it up by name too.
        if venue["location"]["id"]:
            loc, loc_action = await app.get_location(
                location_id=venue["location"]["id"]), "exists"
        else:
            match = [l for l in await app.list_locations(org_id=org_id)
                     if l.name == venue["location"]["name"]]
            loc, loc_action = (match[0], "reuse") if match else (None, "create")

        fname = venue["obstacles_fragment"]["name"]
        if venue["obstacles_fragment"]["id"]:
            fr, frag_action = await app.get_fragment(
                fragment_id=venue["obstacles_fragment"]["id"]), "exists"
        else:
            match = [f for f in await app.list_fragments(org_id=org_id, show_public=False)
                     if f.name == fname]
            fr, frag_action = (match[0], "reuse") if match else (None, "create")

        # ---------------------------------------------------------------- plan
        plan = []
        if loc_action == "create":
            plan.append(f"create location {venue['location']['name']!r}")
        elif loc_action == "reuse":
            plan.append(f"reuse location {loc.id} (already exists, matched by name)")
        if frag_action == "create":
            plan.append(f"create fragment {fname!r} from local JSON")
        elif frag_action == "reuse":
            plan.append(f"reuse fragment {fr.id} (already exists, matched by name)")
        if args.push_fragment and frag_action in ("exists", "reuse"):
            plan.append("push local JSON into that fragment (OVERWRITES it)")
        if loc_action in ("create", "reuse") or frag_action in ("create", "reuse"):
            plan.append(f"write ids back to {venue_path.name}")
        print(f"  plan      : {', '.join(plan) if plan else 'nothing to do'}")

        if not args.apply:
            print("\n[dry run] nothing was changed. re-run with --apply")
            return

        # --------------------------------------------------------------- apply
        if loc_action == "exists":
            print(f"\n  location exists: {loc.name} ({loc.id})")
        else:
            if loc_action == "create":
                loc = await app.create_location(org_id=org_id, name=venue["location"]["name"])
                print(f"\n  created location: {loc.name} ({loc.id})")
            else:
                print(f"\n  location already exists by name, reusing: {loc.id}")
            venue["location"]["id"] = loc.id
            changed = True

        if frag_action == "exists":
            print(f"  fragment exists: {fr.name} ({fr.id})")
        else:
            if frag_action == "create":
                fr = await app.create_fragment(org_id=org_id, name=fname, config=frag_cfg)
                print(f"  created fragment: {fr.name} ({fr.id})")
            else:
                print(f"  fragment already exists by name, reusing: {fr.id}")
            venue["obstacles_fragment"]["id"] = fr.id
            changed = True

        # A freshly created fragment already holds the local JSON; only an existing
        # one needs pushing, and only when asked.
        if args.push_fragment and frag_action in ("exists", "reuse"):
            await app.update_fragment(fragment_id=fr.id, name=fr.name, config=frag_cfg)
            print(f"  pushed {frag_src.name} into {fr.name}")
    finally:
        client.close()

    if changed:
        venue_path.write_text(json.dumps(venue, indent=2) + "\n")
        print(f"\n  wrote ids back to {venue_path.relative_to(ROOT)} - commit it so the team gets them")
    print("\nOK")


if __name__ == "__main__":
    asyncio.run(main())
