#!/usr/bin/env python3
"""Generate a module reference by INTERROGATING the instance. Stdlib only.

Why this exists: reference files written from memory are wrong. This skill
shipped a recipe using `stage_id.is_close` — an Odoo 16 field that does not
exist in 18 — and it survived until a live test caught it. Everything in a
generated reference comes from a call whose output is shown.

    python3 explore_module.py --list              what is worth exploring
    python3 explore_module.py sales               generate references/sales.md
    python3 explore_module.py --models x.y,z.w --name custom   any module

The generated file has two parts:

    AUTO-GENERATED   rebuilt on every run — never edit by hand
    NOTES            your pitfalls, preserved across regenerations

So a reference is never stale: re-run it after an Odoo upgrade or a module
install and the facts refresh while your notes survive.
"""
import ast
import math
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from odoo_client import connect_cli as connect, OdooError  # noqa: E402

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF_DIR = os.path.join(SKILL_DIR, "references")

# Known groupings. Anything not listed can still be explored with --models.
KNOWN = {
    "accounting": ["account.move", "account.move.line", "account.payment",
                   "account.journal", "account.tax", "purchase.order"],
    "sales": ["sale.order", "sale.order.line", "product.template",
              "product.pricelist"],
    "partners": ["res.partner", "res.partner.category", "res.users"],
    "projects": ["project.project", "project.task", "account.analytic.line",
                 "documents.document"],
    "crm": ["crm.lead", "crm.stage", "crm.team", "crm.tag"],
    "helpdesk": ["helpdesk.ticket", "helpdesk.team", "helpdesk.stage",
                 "helpdesk.sla"],
    "inventory": ["stock.picking", "stock.quant", "stock.move", "stock.location"],
    "hr": ["hr.employee", "hr.department", "hr.leave", "hr.expense"],
    "manufacturing": ["mrp.production", "mrp.bom", "mrp.workorder"],
    "pos": ["pos.order", "pos.session", "pos.config"],
    "events": ["event.event", "event.registration"],
    "marketing": ["mailing.mailing", "mailing.contact"],
    "website": ["website", "website.page", "blog.post"],
    "elearning": ["slide.channel", "slide.slide"],
    "field_service": ["fsm.order", "fsm.location"],
    "repair": ["repair.order"],
    "rental": ["sale.rental.schedule"],
    "surveys": ["survey.survey", "survey.user_input"],
    "appointments": ["appointment.type", "calendar.event"],
    "subscriptions": ["sale.order"],
}

STATE_HINTS = ("state", "status", "stage", "type", "kind")


def companies(odoo):
    return [c["id"] for c in odoo.search_read("res.company", [], ["id"])]


def probe(odoo, model, ctx):
    try:
        n = odoo.search_count(model, [], ctx)
    except OdooError:
        return None
    try:
        f = odoo.fields_get(model, [], ["string", "type", "selection", "relation"])
    except OdooError:
        f = {}
    try:
        acts = odoo.search_count("ir.actions.act_window", [["res_model", "=", model]])
    except OdooError:
        acts = 0
    states = [k for k, v in f.items()
              if v.get("type") == "selection" and any(h in k for h in STATE_HINTS)]
    return {"records": n, "fields": f, "state_fields": states, "actions": acts}


def score(info_list):
    """Volume alone does not justify a reference; states and actions are where
    mistakes happen."""
    recs = sum(i["records"] for i in info_list)
    return (math.log10(max(recs, 1)) * 2
            + sum(i["actions"] for i in info_list) * 0.3
            + sum(len(i["state_fields"]) for i in info_list) * 0.5
            + len(info_list))


def cmd_list(odoo, ctx):
    print("Exploring installed modules...\n")
    rows = []
    for name, models in KNOWN.items():
        infos = []
        for m in models:
            p = probe(odoo, m, ctx)
            if p:
                infos.append(p)
        if infos:
            rows.append((name, sum(i["records"] for i in infos), len(infos), score(infos)))

    rows.sort(key=lambda r: -r[3])
    have = {f[:-3] for f in os.listdir(REF_DIR)} if os.path.isdir(REF_DIR) else set()

    print(f"{'MODULE':<16} {'RECORDS':>9} {'MODELS':>7} {'SCORE':>6}  {'REFERENCE':<12} SUGGESTION")
    print("-" * 78)
    for name, recs, nmod, sc in rows:
        ref = "exists" if name in have else "—"
        if name in have:
            sug = "re-run to refresh"
        elif sc >= 14:
            sug = "worth a reference"
        elif sc >= 8:
            sug = "optional"
        else:
            sug = "not worth it — too little data"
        print(f"{name:<16} {recs:>9} {nmod:>7} {sc:>6.1f}  {ref:<12} {sug}")

    print("\nGenerate one with:  python3 explore_module.py <module>")
    print("Custom module:      python3 explore_module.py --models a.b,c.d --name mine")


def selection_values(odoo, model, field, meta):
    sel = meta.get("selection")
    if isinstance(sel, list) and sel:
        return sel
    try:  # some selections are computed and only resolve via fields_get on the field
        f = odoo.fields_get(model, [field], ["selection"])
        return f.get(field, {}).get("selection") or []
    except OdooError:
        return []


def distribution(odoo, model, field, ctx, limit=8):
    try:
        rows = odoo.read_group(model, [], [], [field], ctx)
    except OdooError:
        return []
    out = []
    for r in rows:
        v = r.get(field)
        if isinstance(v, list):
            v = v[1] if len(v) > 1 else v[0]
        out.append((v, r.get("__count") or r.get(f"{field}_count") or 0))
    return sorted(out, key=lambda x: -(x[1] or 0))[:limit]


def signed_twins(fields):
    """Fields whose *_signed twin is the one you must sum. This is the trap
    that inflated a €1,2M total to €14,3M by adding EUR and CZK."""
    return sorted(k for k in fields if k.endswith("_signed")
                  and k[:-len("_signed")] in fields)


def menus_for(odoo, model, ctx):
    """What the user can actually click, with the domain each entry applies."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from view_first import resolve_action
    except Exception:
        return []
    try:
        menus = odoo.search_read(
            "ir.ui.menu", [["action", "!=", False]], ["name", "action", "complete_name"])
    except OdooError:
        return []
    out = []
    for m in menus:
        act = str(m.get("action") or "")
        if not act.startswith("ir.actions.act_window,"):
            continue
        try:
            r = resolve_action(odoo, int(act.split(",")[1]))
        except Exception:
            continue
        if r.get("model") != model:
            continue
        count = "?"
        if not r["needs_runtime_eval"]:
            try:
                count = odoo.search_count(model, r["domain"], ctx)
            except OdooError:
                count = "?"
        naive = None
        if r.get("base_domain") and r.get("search_defaults"):
            try:
                naive = odoo.search_count(
                    model, list(ast.literal_eval(r["base_domain"])), ctx)
            except Exception:
                naive = None
        out.append({"path": m.get("complete_name") or m["name"],
                    "domain": r["domain"], "count": count, "naive": naive})
    return out


def action_methods(odoo, model):
    """Public action_* / button_* methods: the write surface of the model."""
    try:
        names = odoo.call("ir.model.fields", "search_read",
                          [[["model", "=", model]]], {"fields": ["name"], "limit": 1})
    except OdooError:
        pass
    found = set()
    try:
        views = odoo.search_read("ir.ui.view",
                                 [["model", "=", model], ["type", "=", "form"]],
                                 ["arch_db"], limit=6)
        for v in views:
            for m in re.finditer(r'name="(action_[a-z0-9_]+|button_[a-z0-9_]+)"',
                                 v.get("arch_db") or ""):
                found.add(m.group(1))
    except OdooError:
        pass
    return sorted(found)


def preserved_notes(path):
    """Keep everything after the NOTES marker so regeneration never destroys
    hand-written knowledge."""
    marker = "## NOTES — hand-written, preserved across regenerations"
    if os.path.exists(path):
        txt = open(path).read()
        if marker in txt:
            return txt.split(marker, 1)[1]
    return ("\n\nAdd pitfalls here as you hit them. This section survives\n"
            "`explore_module.py` re-runs; everything above it does not.\n")


def generate(odoo, name, models, ctx):
    cids = ctx.get("allowed_company_ids")
    L = []
    A = L.append
    A(f"# {name} — generated reference\n")
    A(f"> Generated by `explore_module.py {name}` on "
      f"{datetime.now().strftime('%Y-%m-%d %H:%M')} against "
      f"`{odoo.db or odoo.base}`.\n> Every figure below came from a call to "
      f"this instance. Re-run to refresh.\n")
    A(f"Company context used: `{{'allowed_company_ids': {cids}}}`\n")

    present = []
    for m in models:
        p = probe(odoo, m, ctx)
        if p:
            present.append((m, p))
        else:
            A(f"- `{m}` — **not installed on this instance**")
    if not present:
        A("\nNothing from this module exists here.")
        return "\n".join(L)

    A("\n## Models and volumes\n")
    A("| Model | Records | Fields | Menus/actions |")
    A("|---|---:|---:|---:|")
    for m, p in present:
        A(f"| `{m}` | {p['records']} | {len(p['fields'])} | {p['actions']} |")

    for m, p in present:
        A(f"\n---\n\n## `{m}`\n")

        twins = signed_twins(p["fields"])
        if twins:
            A("### Sum the `_signed` twin, never the plain field\n")
            A("These fields are in the record's own currency; the `_signed`")
            A("version is converted to company currency and sign-correct:\n")
            for t in twins:
                A(f"- `{t[:-len('_signed')]}` → use **`{t}`**")
            A("")

        if p["state_fields"]:
            A("### State fields — real values, read from the instance\n")
            for sf in p["state_fields"][:5]:
                vals = selection_values(odoo, m, sf, p["fields"][sf])
                if not vals:
                    continue
                A(f"`{sf}`:\n")
                dist = dict(distribution(odoo, m, sf, ctx))
                A("| key | label | records |")
                A("|---|---|---:|")
                for k, lab in vals[:12]:
                    A(f"| `{k}` | {lab} | {dist.get(k, dist.get(lab, 0))} |")
                A("")

        # stage_id is a RELATION, not a selection — but it is the state field
        # people actually mean ("open tickets", "tasks in progress"). Missing
        # it is how "53 tickets" gets reported as the answer to "how many open".
        #
        # NOT state_id: on crm.lead / res.partner that is the geographic
        # province (res.country.state). Including it once produced a 46 KB
        # table of every province on earth. Only walk relations that point at
        # a stage-like model AND are small enough to be a workflow.
        STAGE_MODELS = ("stage", "state") 
        for rel_state in ("stage_id",):
            if p["fields"].get(rel_state, {}).get("type") != "many2one":
                continue
            rel_model = p["fields"][rel_state].get("relation") or ""
            if "country" in rel_model or "res.country" in rel_model:
                continue
            if not any(s in rel_model for s in STAGE_MODELS):
                continue
            try:
                cols = odoo.fields_get(rel_model, [], ["type"])
                want = [c for c in ("name", "sequence", "fold", "is_close")
                        if c in cols]
                stages = odoo.search_read(rel_model, [], want, limit=40,
                                          order="sequence" if "sequence" in cols else None,
                                          context=ctx)
            except OdooError:
                continue
            # A workflow has a handful of stages. Dozens means it is a lookup
            # table (provinces, countries, tags), not a state machine.
            if not stages or len(stages) > 25:
                continue
            dist = dict(distribution(odoo, m, rel_state, ctx, limit=40))
            closed_flag = "fold" if "fold" in want else ("is_close" if "is_close" in want else None)
            A(f"### `{rel_state}` → `{rel_model}` — the stage people mean by "
              f"\"open\"\n")
            A("| stage | closed? | records |")
            A("|---|---|---:|")
            open_total = 0
            for s in stages:
                nm = s.get("name")
                closed = bool(s.get(closed_flag)) if closed_flag else False
                cnt = dist.get(nm, 0)
                if not closed:
                    open_total += cnt
                A(f"| {nm} | {'closed' if closed else 'open'} | {cnt} |")
            A("")
            if closed_flag:
                A(f"**Open = `{rel_state}.{closed_flag} = False` → {open_total} "
                  f"records, against {p['records']} in total.** Reporting the "
                  f"total as the answer to \"how many open\" overstates it by "
                  f"{p['records']/max(open_total,1):.1f}×.\n")
                A("```python")
                A(f'odoo.search_count("{m}", [["{rel_state}.{closed_flag}", "=", False]], CTX)')
                A("```\n")

        mm = menus_for(odoo, m, ctx)
        if mm:
            A("### What the user sees in the menus\n")
            A("The count is the **resolved** one (action domain + the menu's")
            A("`search_default_*`). Where they differ, the naive figure is what")
            A("a query on the action domain alone would wrongly report.\n")
            A("| Menu | Records | Naive | Domain |")
            A("|---|---:|---:|---|")
            for e in mm[:12]:
                naive = e["naive"] if e["naive"] is not None else ""
                warn = ""
                if isinstance(e["count"], int) and isinstance(naive, int) and naive > e["count"]:
                    factor = naive / max(e["count"], 1)
                    warn = f" ⚠️ {factor:.1f}×"
                dom = str(e["domain"])
                dom = dom[:60] + "…" if len(dom) > 60 else dom
                A(f"| {e['path']} | {e['count']} | {naive}{warn} | `{dom}` |")
            A("")

        acts = action_methods(odoo, m)
        if acts:
            A("### Action methods found in the form views\n")
            A("These are the write surface. Classify before calling — see")
            A("`safety_layer.py`; anything not whitelisted is refused.\n")
            A("```\n" + "  ".join(acts[:24]) + "\n```\n")

        rel = [(k, v.get("relation")) for k, v in p["fields"].items()
               if v.get("type") in ("many2one",) and v.get("relation")
               and k in ("partner_id", "company_id", "user_id", "project_id",
                         "move_id", "order_id", "team_id", "stage_id",
                         "employee_id", "product_id")]
        if rel:
            A("### Key relations\n")
            for k, r in sorted(rel):
                A(f"- `{k}` → `{r}`")
            A("")

    A("\n---\n")
    A("## NOTES — hand-written, preserved across regenerations")
    return "\n".join(L)


def main():
    args = [a for a in sys.argv[1:]]
    odoo = connect()
    ctx = {"allowed_company_ids": companies(odoo)}

    if not args or args[0] in ("--list", "-l"):
        cmd_list(odoo, ctx)
        return

    if args[0] == "--models":
        models = [m.strip() for m in args[1].split(",") if m.strip()]
        name = args[args.index("--name") + 1] if "--name" in args else "custom"
    else:
        name = args[0]
        if name not in KNOWN:
            print(f"Unknown module '{name}'. Options:\n  " +
                  ", ".join(sorted(KNOWN)) +
                  "\n\nFor anything else:\n"
                  "  python3 explore_module.py --models a.b,c.d --name yourname")
            raise SystemExit(1)
        models = KNOWN[name]

    os.makedirs(REF_DIR, exist_ok=True)
    path = os.path.join(REF_DIR, f"{name}.md")
    notes = preserved_notes(path)
    body = generate(odoo, name, models, ctx)

    # A reference the agent cannot afford to open is worse than none: it will
    # be skipped and the agent will guess instead. 12 KB is roughly 3k tokens.
    # This ceiling applies to GENERATED files — if one exceeds it the module
    # was split wrongly. Hand-written references (writing.md, collaboration.md)
    # may run longer: verified patterns cannot be compressed without losing
    # the evidence that makes them trustworthy.
    SIZE_LIMIT = 12 * 1024
    if len(body) > SIZE_LIMIT:
        print(f"WARNING: generated {len(body)/1024:.1f} KB, over the "
              f"{SIZE_LIMIT/1024:.0f} KB budget.")
        print("A reference this large will not be loaded. Usually it means a")
        print("lookup table was mistaken for a workflow, or too many models")
        print("were passed at once. Split it:")
        print(f"  python3 explore_module.py --models <fewer,models> --name {name}")
        big = sorted(re.split(r"\n(?=### )", body), key=len, reverse=True)[:2]
        for b in big:
            print(f"  largest section: {len(b)/1024:.1f} KB — {b.splitlines()[0][:60]}")
        raise SystemExit(1)

    with open(path, "w") as f:
        f.write(body + notes)
    print(f"Written: {path} ({os.path.getsize(path)/1024:.1f} KB)")
    print("The NOTES section at the bottom was preserved.")


if __name__ == "__main__":
    main()
