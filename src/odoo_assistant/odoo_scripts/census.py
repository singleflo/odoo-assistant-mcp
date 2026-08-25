#!/usr/bin/env python3
"""Census — build the instance profile. Stdlib only.

Discovery of STRUCTURE (which modules, which menus) is not enough. To answer
"how much did ACME invoice?" without five exploratory calls, the agent needs
to know what is INSIDE: volumes, top entities, distributions, and the real
vocabulary (journals are called "FATT" here — that is how the user will
refer to them).

Writes to  $ODOO_PROFILE_DIR/<db>.json  (default: the per-user data dir).
Never loaded whole into context — read slices with query.py.

    python3 census.py            # full census
    python3 census.py --quick    # fingerprint only (4 calls, drift check)
"""
import hashlib
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from odoo_client import connect_cli as connect, OdooError  # noqa: E402

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


def profile_path(odoo):
    os.makedirs(PROFILE_DIR, exist_ok=True)
    name = odoo.db or odoo.base.replace("://", "_").replace("/", "_")
    return os.path.join(PROFILE_DIR, f"{name}.json")


# --------------------------------------------------------------- fingerprint
def fingerprint(odoo):
    """4 calls, ~0.2s. Two orders of magnitude cheaper than a full census
    (~1000 calls), which is what makes drift detection worth running at the
    start of every session."""
    info = odoo.info()
    mods = odoo.search_read("ir.module.module", [["state", "=", "installed"]],
                            ["name", "write_date"])
    names = sorted(m["name"] for m in mods)
    return {
        "odoo_version": info["odoo_version"],
        "edition": info["edition"],
        "transport": info["transport"],
        "module_count": len(mods),
        "modules_hash": hashlib.sha256("|".join(names).encode()).hexdigest()[:16],
        "last_module_change": max((m["write_date"] for m in mods), default=""),
        "menu_count": odoo.search_count("ir.ui.menu", []),
        "company_ids": sorted(c["id"] for c in odoo.search_read(
            "res.company", [], ["id"])),
        "taken_at": datetime.now().isoformat(timespec="seconds"),
    }


def has_model(odoo, model):
    # Verified live: querying an absent model writes an ERROR traceback into
    # someone else's production log, so establish existence without provoking it.
    if not odoo.search_count("ir.model", [["model", "=", model]]):
        return False
    try:
        odoo.search_count(model, [])
        return True
    except OdooError:
        return False


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


# ------------------------------------------------------------------- census
def census(odoo):
    # TWO readings, deliberately kept apart:
    #   ctx      includes archived records  -> the historical total
    #   ctx_live excludes them              -> what the user sees on screen
    # Mixing them is how "53 tickets" silently becomes "55".
    ctx = {"active_test": False}
    companies = odoo.search_read("res.company", [], ["id", "name", "country_id"])
    cids = [c["id"] for c in companies]
    ctx["allowed_company_ids"] = cids
    ctx_live = {"allowed_company_ids": cids}          # archived excluded
    out = {
        "fingerprint": fingerprint(odoo),
        "companies": companies,
        "areas": {},
        "vocabulary": {},
        "anomalies": {},
    }

    # ---- Accounting -------------------------------------------------------
    if has_model(odoo, "account.move"):
        by_type = {}
        for mt in ("out_invoice", "in_invoice", "out_refund", "in_refund", "entry"):
            by_type[mt] = odoo.search_count("account.move", [["move_type", "=", mt]], ctx)
        by_state = _safe(lambda: odoo.read_group(
            "account.move", [["move_type", "=", "out_invoice"]],
            ["amount_total:sum"], ["state"], ctx), [])
        out["areas"]["accounting"] = {
            "note_totals": ("by_move_type numbers are per type. Do NOT add them "
                            "up and call the result 'invoices': entry are "
                            "bookkeeping records, not invoices."),
            "by_move_type": by_type,
            "customer_invoices_by_state": [
                {"state": g.get("state"), "count": g.get("__count") or g.get("state_count"),
                 "amount_total": g.get("amount_total")} for g in by_state],
            "invoiced_total_company_currency": _safe(lambda: round(
                (odoo.read_group("account.move",
                    [["move_type", "=", "out_invoice"], ["state", "=", "posted"]],
                    ["amount_total_signed:sum"], [], ctx)
                 or [{}])[0].get("amount_total_signed") or 0, 2)),
            "outstanding_receivable": _safe(lambda: round(
                (odoo.read_group("account.move",
                    [["move_type", "=", "out_invoice"], ["state", "=", "posted"],
                     ["payment_state", "not in", ["paid", "reversed"]]],
                    ["amount_residual_signed:sum"], [], ctx)
                 or [{}])[0].get("amount_residual_signed") or 0, 2)),
            "note_currency": ("Amounts are amount_*_signed = company currency. "
                              "NEVER sum amount_total: it mixes currencies."),
            "payments": _safe(lambda: odoo.search_count("account.payment", [], ctx)),
            "journals": _safe(lambda: odoo.search_count("account.journal", [], ctx)),
        }
        out["vocabulary"]["journals"] = _safe(lambda: odoo.search_read(
            "account.journal", [], ["name", "code", "type", "company_id"]), [])
        # overdue = posted, not paid, due in the past
        today = datetime.now().strftime("%Y-%m-%d")
        out["anomalies"]["overdue_customer_invoices"] = _safe(lambda: odoo.search_count(
            "account.move",
            [["move_type", "=", "out_invoice"], ["state", "=", "posted"],
             ["payment_state", "not in", ["paid", "reversed"]],
             ["invoice_date_due", "<", today]], ctx))
        out["anomalies"]["draft_customer_invoices"] = _safe(lambda: odoo.search_count(
            "account.move", [["move_type", "=", "out_invoice"], ["state", "=", "draft"]], ctx))

    # ---- Sales ------------------------------------------------------------
    if has_model(odoo, "sale.order"):
        out["areas"]["sales"] = {
            "orders_total_ever": odoo.search_count("sale.order", [], ctx),
            "quotations_PENDING": _safe(lambda: odoo.search_count(
                "sale.order", [["state", "in", ["draft", "sent"]]], ctx_live)),
            "orders_CONFIRMED": _safe(lambda: odoo.search_count(
                "sale.order", [["state", "=", "sale"]], ctx)),
            "by_state": _safe(lambda: [
                {"state": g.get("state"), "count": g.get("__count") or g.get("state_count"),
                 "amount_total": g.get("amount_total")}
                for g in odoo.read_group("sale.order", [], ["amount_total:sum"], ["state"], ctx)], []),
            "order_lines": _safe(lambda: odoo.search_count("sale.order.line", [], ctx)),
            "products": _safe(lambda: odoo.search_count("product.template", [], ctx)),
        }
        out["anomalies"]["confirmed_not_invoiced"] = _safe(lambda: odoo.search_count(
            "sale.order", [["state", "=", "sale"], ["invoice_status", "=", "to invoice"]], ctx))

    # ---- Partners ---------------------------------------------------------
    out["areas"]["partners"] = {
        "companies": odoo.search_count("res.partner", [["is_company", "=", True]], ctx),
        "contacts": odoo.search_count("res.partner", [["is_company", "=", False]], ctx),
        "customers": _safe(lambda: odoo.search_count(
            "res.partner", [["customer_rank", ">", 0]], ctx)),
        "suppliers": _safe(lambda: odoo.search_count(
            "res.partner", [["supplier_rank", ">", 0]], ctx)),
        "users": odoo.search_count("res.users", [], ctx),
    }

    # ---- CRM --------------------------------------------------------------
    if has_model(odoo, "crm.lead"):
        out["areas"]["crm"] = {
            "total_including_archived": odoo.search_count("crm.lead", [], ctx),
            "active_only": odoo.search_count("crm.lead", []),
            "by_type": _safe(lambda: [
                {"type": g.get("type"), "count": g.get("__count") or g.get("type_count")}
                for g in odoo.read_group("crm.lead", [], [], ["type"], ctx)], []),
            "by_stage": _safe(lambda: [
                {"stage": (g.get("stage_id") or [None, "?"])[1],
                 "count": g.get("__count") or g.get("stage_id_count"),
                 "expected_revenue": g.get("expected_revenue")}
                for g in odoo.read_group("crm.lead", [], ["expected_revenue:sum"],
                                         ["stage_id"], ctx)], []),
        }
        out["vocabulary"]["crm_stages"] = _safe(lambda: odoo.search_read(
            "crm.stage", [], ["name", "sequence"]), [])
        out["vocabulary"]["crm_teams"] = _safe(lambda: odoo.search_read(
            "crm.team", [], ["name"]), [])

    # ---- Projects ---------------------------------------------------------
    if has_model(odoo, "project.project"):
        out["areas"]["projects"] = {
            "projects": odoo.search_count("project.project", [], ctx),
            "tasks": _safe(lambda: odoo.search_count("project.task", [], ctx)),
            "timesheet_lines": _safe(lambda: odoo.search_count(
                "account.analytic.line", [["project_id", "!=", False]], ctx)),
            "timesheet_HOURS": _safe(lambda: round(sum(
                g.get("unit_amount") or 0 for g in odoo.read_group(
                    "account.analytic.line", [["project_id", "!=", False]],
                    ["unit_amount:sum"], ["project_id"], ctx)), 1)),
            "note": "HOURS is the sum people mean by 'hours logged'; lines is the record count.",
        }
        out["anomalies"]["tasks_without_assignee"] = _safe(lambda: odoo.search_count(
            "project.task", [["user_ids", "=", False]], ctx))

    # ---- Helpdesk ---------------------------------------------------------
    if has_model(odoo, "helpdesk.ticket"):
        # Filter through the relation (stage_id.fold) instead of collecting
        # stage ids first: reading stages without the company context returns
        # only one company's stages, which silently under-counts.
        out["areas"]["helpdesk"] = {
            "tickets_total_ever": odoo.search_count("helpdesk.ticket", [], ctx),
            "tickets_OPEN": _safe(lambda: odoo.search_count(
                "helpdesk.ticket", [["stage_id.fold", "=", False]], ctx_live)),
            "note": ("OPEN = stage not folded, archived excluded — this is the "
                     "number the user sees. total_ever includes archived and "
                     "closed tickets."),
            "by_team": _safe(lambda: [
                {"team": (g.get("team_id") or [None, "?"])[1],
                 "count": g.get("__count") or g.get("team_id_count")}
                for g in odoo.read_group("helpdesk.ticket", [], [], ["team_id"], ctx)], []),
        }
        out["vocabulary"]["helpdesk_teams"] = _safe(lambda: odoo.search_read(
            "helpdesk.team", [], ["name"]), [])

    # ---- Subscriptions ----------------------------------------------------
    # Verified live: Odoo 16 has no subscription_state; mentioning it writes a
    # traceback into someone else's production log even when _safe swallows it.
    sale_fields = (_safe(lambda: odoo.fields_get(
        "sale.order", [], ["type"]), {}) if has_model(odoo, "sale.order") else {})
    if "subscription_state" in sale_fields:
        subs = _safe(lambda: odoo.search_count(
            "sale.order", [["subscription_state", "!=", False]], ctx))
        if subs:
            out["areas"]["subscriptions"] = {
                "subscriptions_total_ever": subs,
                "subscriptions_RUNNING": _safe(lambda: odoo.search_count(
                    "sale.order",
                    [["subscription_state", "in", ["3_progress", "4_paused"]]], ctx_live)),
                "note": "RUNNING = 3_progress + 4_paused. Total includes churned/renewed.",
                "by_state": _safe(lambda: [
                    {"state": g.get("subscription_state"),
                     "count": g.get("__count") or g.get("subscription_state_count")}
                    for g in odoo.read_group(
                        "sale.order", [["subscription_state", "!=", False]], [],
                        ["subscription_state"], ctx)], []),
            }

    # ---- Top customers by invoiced amount ---------------------------------
    out["top_customers"] = _safe(lambda: sorted([
        {"partner": (g.get("partner_id") or [None, "?"])[1],
         "partner_id": (g.get("partner_id") or [None])[0],
         "invoiced": g.get("amount_total_signed") or g.get("amount_total")}
        for g in odoo.read_group(
            "account.move",
            [["move_type", "=", "out_invoice"], ["state", "=", "posted"]],
            ["amount_total_signed:sum"], ["partner_id"], ctx)
    ], key=lambda x: -(x["invoiced"] or 0))[:20], [])

    # Which optional areas are ABSENT. Without this the agent either invents
    # ("Helpdesk not installed" when 53 tickets exist) or asks the user for
    # clarification about a module that cannot answer anyway.
    probes = {
        "inventory / stock levels": "stock.quant",
        "time off / holidays": "hr.leave",
        "employees": "hr.employee",
        "manufacturing": "mrp.production",
        "purchase orders": "purchase.order",
        "point of sale": "pos.order",
    }
    present, absent = {}, []
    for label, model in probes.items():
        if has_model(odoo, model):
            present[label] = _safe(lambda m=model: odoo.search_count(m, [], ctx))
        else:
            absent.append(f"{label} ({model})")
    # "installed" and "used" are different questions. A module can be present
    # with zero data — reporting it as a live capability misleads.
    custom_usage = {}
    for label, model, field in (
        ("preinvoices", "account.move", "is_preinvoice"),
        ("leads with a source", "crm.lead", "source_id"),
    ):
        try:
            flds = odoo.fields_get(model, [], ["type"])
            if field in flds:
                dom = [[field, "=", True]] if flds[field].get("type") == "boolean" \
                    else [[field, "!=", False]]
                custom_usage[label] = odoo.search_count(model, dom, ctx)
        except Exception:
            pass
    if custom_usage:
        out["installed_but_check_usage"] = custom_usage

    # The authoritative list of in-house modules, read from ir.module.module.
    # Without it the agent DEDUCES customisations from the data and invents
    # plausible names — a cold-start test produced "crm_extended" and
    # "accounting_extensions", neither of which exists.
    try:
        mods = odoo.search_read(
            "ir.module.module",
            [["state", "=", "installed"], ["author", "not like", "Odoo"]],
            ["name", "shortdesc", "author"])
        out["custom_modules"] = sorted(
            [{"name": m["name"], "description": m.get("shortdesc"),
              "author": m.get("author")} for m in mods],
            key=lambda m: m["name"])
        out["custom_modules_note"] = (
            "Authoritative list from ir.module.module. NEVER infer a module "
            "name from the data — if it is not in this list, it does not exist.")
    except Exception:
        pass

    out["also_present"] = present
    out["NOT_AVAILABLE"] = {
        "areas": absent,
        "note": ("These models do not exist on this instance. Questions about "
                 "them cannot be answered — say so plainly instead of asking "
                 "the user to clarify."),
    }
    return out


def main():
    odoo = connect()
    quick = "--quick" in sys.argv
    path = profile_path(odoo)

    if quick:
        fp = fingerprint(odoo)
        old = None
        if os.path.exists(path):
            old = json.load(open(path)).get("fingerprint")
        print(json.dumps(fp, indent=2))
        if old:
            changed = [k for k in ("edition", "module_count", "modules_hash",
                                   "menu_count", "company_ids")
                       if old.get(k) != fp.get(k)]
            if changed:
                print(f"\nDRIFT DETECTED — changed: {', '.join(changed)}")
                print("The stored profile is stale. Run: python3 census.py")
            else:
                print(f"\nNo drift. Profile from {old.get('taken_at')} is current.")
        else:
            print(f"\nNo profile yet at {path}. Run: python3 census.py")
        return

    print("Building census...", file=sys.stderr)
    data = census(odoo)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    size = os.path.getsize(path)
    print(f"Profile written: {path} ({size/1024:.0f} KB)")
    for area, d in data["areas"].items():
        first = list(d.items())[0]
        print(f"  {area:<16} {first[0]}={first[1]}")


if __name__ == "__main__":
    main()
