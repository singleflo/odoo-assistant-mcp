#!/usr/bin/env python3
"""View-first: resolve what a MENU actually shows. Stdlib only.

Odoo applies TWO filters to a menu: the action's `domain` AND the
`search_default_*` entries from its context, which map to named filters in
the search view. The user sees the intersection.

Reading only the domain over-counts. Measured on real data:

    Credit Notes      domain only 389  ->  real 16   (24,3x)
    Vendor Refunds    domain only 656  ->  real  3  (218,7x)

The same defect appears on vanilla Odoo Community, so it is structural,
not an artifact of one instance.

Usage:
    python3 view_first.py "Credit Notes"     # find a menu and resolve it
    python3 view_first.py --model account.move
"""
import ast
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from odoo_client import connect_cli as connect, OdooError  # noqa: E402

_view_cache = {}


def parse_context(raw):
    if not raw or raw == "{}":
        return {}
    try:
        return ast.literal_eval(raw)
    except Exception:
        # Contexts holding uid/active_id are not literal-evaluable; pull out
        # the search_default_* keys textually.
        return {m.group(1): m.group(2).strip()
                for m in re.finditer(r"'(search_default_\w+)'\s*:\s*([^,}]+)", raw)}


def has_dynamic(expr):
    return any(t in str(expr) for t in
               ("uid", "active_id", "context_today", "datetime", "allowed_company_ids"))


def search_filters(odoo, model, search_view_id=None):
    """{filter_name: domain} from the search view.

    An action may point to a SPECIFIC search view; using the model default
    instead silently loses its filters (that was a real bug: "My Quotations"
    resolved to nothing because its filter lives in view 1701, not the
    default one)."""
    ck = (model, search_view_id)
    if ck in _view_cache:
        return _view_cache[ck]
    try:
        res = odoo.call(model, "get_views",
                        [[[search_view_id or False, "search"]]], {})
        root = ET.fromstring(res["views"]["search"]["arch"])
        doms, groups = {}, {}
        for f in root.iter("filter"):
            name = f.get("name")
            if not name:
                continue
            if f.get("domain"):
                doms[name] = f.get("domain")
            elif f.get("context") and "group_by" in f.get("context"):
                groups[name] = f.get("context")
        _view_cache[ck] = (doms, groups)
    except Exception:
        _view_cache[ck] = ({}, {})
    return _view_cache[ck]


def resolve_action(odoo, action_id):
    """Return the domain the user actually sees for an act_window."""
    a = odoo.call("ir.actions.act_window", "read", [[action_id]],
                  {"fields": ["name", "domain", "context", "res_model",
                              "search_view_id"]})[0]
    model = a.get("res_model")
    dom_raw = a.get("domain")
    has_dom = bool(dom_raw) and dom_raw not in ("False", "[]", None)

    svid = a.get("search_view_id")
    svid = svid[0] if isinstance(svid, list) and svid else None

    ctx = parse_context(a.get("context", "{}"))
    sds = [k for k in ctx if k.startswith("search_default_") and ctx[k]]

    sd_domains, unresolved = [], []
    if sds:
        filters, groups = search_filters(odoo, model, svid)
        for k in sds:
            fname = k[len("search_default_"):]
            if fname in filters:
                sd_domains.append(filters[fname])
            elif fname not in groups:
                unresolved.append(fname)

    composed, dynamic = [], False
    if has_dom:
        if has_dynamic(dom_raw):
            dynamic = True
        else:
            composed += list(ast.literal_eval(dom_raw))
    for sd in sd_domains:
        if has_dynamic(sd):
            dynamic = True
        else:
            composed += list(ast.literal_eval(sd))

    return {
        "action_id": action_id,
        "name": a.get("name"),
        "model": model,
        "base_domain": dom_raw if has_dom else None,
        "search_defaults": sd_domains,
        "domain": composed,
        "needs_runtime_eval": dynamic,
        "unresolved_filters": unresolved,
    }


def find_menus(odoo, text):
    """Menus whose name matches, with their full breadcrumb path."""
    menus = odoo.search_read("ir.ui.menu", [["name", "ilike", text]],
                             ["id", "name", "action", "complete_name"])
    return [m for m in menus
            if str(m.get("action") or "").startswith("ir.actions.act_window,")]


def resolve_menu(odoo, menu):
    aid = int(menu["action"].split(",")[1])
    r = resolve_action(odoo, aid)
    r["menu_path"] = menu.get("complete_name") or menu["name"]
    return r


if __name__ == "__main__":
    odoo = connect()
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)

    if sys.argv[1] == "--model":
        model = sys.argv[2]
        acts = odoo.search_read("ir.actions.act_window",
                                [["res_model", "=", model]], ["id", "name"], limit=20)
        for a in acts:
            r = resolve_action(odoo, a["id"])
            n = odoo.search_count(model, r["domain"]) if not r["needs_runtime_eval"] else "?"
            print(f"  [{a['id']:>5}] {a['name'][:34]:<34} {n:>7}  {r['domain']}")
        raise SystemExit(0)

    for m in find_menus(odoo, sys.argv[1]):
        r = resolve_menu(odoo, m)
        print(f"\n--- {r['menu_path']} (action {r['action_id']}) ---")
        print(f"  model          : {r['model']}")
        print(f"  base domain    : {r['base_domain']}")
        print(f"  search_default : {r['search_defaults']}")
        print(f"  REAL domain    : {r['domain']}")
        if r["needs_runtime_eval"]:
            print("  needs runtime evaluation (uid/active_id/dates)")
        else:
            try:
                print(f"  records        : {odoo.search_count(r['model'], r['domain'])}")
                if r["base_domain"]:
                    naive = odoo.search_count(r["model"], list(ast.literal_eval(r["base_domain"])))
                    print(f"  (domain only)  : {naive}   <- what a naive query would report")
            except OdooError as e:
                print(f"  count failed: {e}")
