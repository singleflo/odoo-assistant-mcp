#!/usr/bin/env python3
"""Read the instance profile — answer without hitting Odoo. Stdlib only.

The profile is never loaded whole into context. Ask for the slice you need.

    python3 query.py                    # overview: what this instance is
    python3 query.py accounting         # one area
    python3 query.py vocabulary         # journals, stages, teams (real names)
    python3 query.py anomalies          # overdue, drafts, orphans
    python3 query.py top-customers      # top 20 by invoiced amount
    python3 query.py --raw accounting   # full JSON of that key
"""
import json
import os
import sys

def _default_profile_dir():
    """Per-user data directory for THIS platform.

    "~/.hermes/odoo/instances" named a retired tool, and "~/.local/share" is a
    Linux answer that on Windows creates a folder literally called "~" beside
    the process. Duplicated from the package's `paths.py` on purpose: these
    scripts stay stdlib-only and importable on their own.
    """
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    elif sys.platform == "darwin":
        base = os.path.join(home, "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    return os.path.join(base, "odoo-assistant", "instances")


PROFILE_DIR = os.path.expanduser(
    os.environ.get("ODOO_PROFILE_DIR") or _default_profile_dir())


def load():
    db = os.environ.get("ODOO_DB")
    path = os.path.join(PROFILE_DIR, f"{db}.json") if db else None
    if not path or not os.path.exists(path):
        cands = [f for f in os.listdir(PROFILE_DIR)] if os.path.isdir(PROFILE_DIR) else []
        if not cands:
            print("No profile found. Build one with:\n"
                  "  python3 census.py", file=sys.stderr)
            raise SystemExit(2)
        path = os.path.join(PROFILE_DIR, cands[0])
    return json.load(open(path)), path


def overview(p):
    fp = p["fingerprint"]
    print(f"Odoo {fp['odoo_version']} {fp['edition']} · {fp['module_count']} modules "
          f"· transport {fp['transport']}")
    print(f"Profile taken {fp['taken_at']}")
    print("\nCompanies:")
    for c in p["companies"]:
        country = (c.get("country_id") or [None, "?"])[1]
        print(f"  [{c['id']}] {c['name']} ({country})")
    print("\nWhat this instance holds:")
    for area, d in p["areas"].items():
        # Fields with an UPPERCASE segment carry the meaning people ask for
        # (tickets_OPEN, timesheet_HOURS). Show those first so the raw totals
        # next to them cannot be mistaken for the answer.
        def rank(k):
            return (0 if any(c.isupper() for c in k) else 1, k)
        bits = []
        for k in sorted([k for k in d if not k.startswith("note")], key=rank):
            v = d[k]
            if isinstance(v, (int, float)):
                bits.append(f"{k}={v}")
            elif isinstance(v, dict):
                bits.append(", ".join(f"{kk}={vv}" for kk, vv in v.items()))
        print(f"  {area:<15} {'; '.join(bits)[:112]}")
    cm = p.get("custom_modules") or []
    if cm:
        print(f"\nIn-house modules: {len(cm)} — authoritative list, never infer "
              f"a module name from the data")
        print("  " + ", ".join(m["name"] for m in cm[:12]) +
              (" …" if len(cm) > 12 else ""))
        print("  full list with descriptions: query.py custom_modules")

    if p.get("also_present"):
        extra = ", ".join(f"{k}={v}" for k, v in p["also_present"].items())
        if extra:
            print(f"\nAlso present: {extra}")

    na = p.get("NOT_AVAILABLE", {}).get("areas")
    if na:
        print("\nNOT on this instance — say so plainly, do not ask to clarify:")
        for a in na:
            print(f"  x {a}")

    if p.get("anomalies"):
        print("\nWorth knowing:")
        for k, v in p["anomalies"].items():
            if v:
                print(f"  {k.replace('_', ' ')}: {v}")


def show(p, key):
    if key in p["areas"]:
        print(json.dumps(p["areas"][key], indent=2, ensure_ascii=False))
    elif key in p:
        print(json.dumps(p[key], indent=2, ensure_ascii=False))
    else:
        print(f"Unknown key '{key}'.\n"
              f"Areas: {', '.join(p['areas'])}\n"
              f"Other: vocabulary, anomalies, top_customers, companies, fingerprint")
        raise SystemExit(1)


if __name__ == "__main__":
    # query.py reads a LOCAL profile — it does not connect to Odoo. But agents
    # pass --url/--key to every script uniformly (and census.py, which BUILDS
    # the profile, needs them). Strip those flags here so they are not mistaken
    # for a profile key — that was the "Unknown key '__url'" bug.
    SKIP_FLAGS = {"--url", "--key", "--db", "--user", "--raw"}
    args = []
    i = 1
    while i < len(sys.argv):
        a = sys.argv[i]
        if a in SKIP_FLAGS:
            i += 2          # skip the flag AND its value
        elif a.startswith("--") and "=" in a:
            i += 1           # --flag=value form
        else:
            args.append(a)
            i += 1
    prof, path = load()
    if not args:
        overview(prof)
        print(f"\n(profile: {path})")
    else:
        show(prof, args[0].replace("-", "_"))
