#!/usr/bin/env python3
"""The four read tools (PRD §5B): search_read, read_record, count_records,
instance_overview.

The three that query Odoo — `search_read`, `read_record`, `count_records` — are
L0 in `safety_layer.classify()` and all three go through `gate()`: not for the
ceiling, which L0 clears at any setting, but for the structural guards `gate()`
runs alongside it. `account.move` holds customer invoices, vendor bills, credit
notes AND raw journal entries in one table, so a query without `move_type`
answers a question nobody asked (3.613 records where the user sees 373). One
code path per tool means that guard cannot be skipped.

`instance_overview` is the exception, and it needs no gate: it reads the local
profile `census.py` wrote and never opens a connection, so there is no call for
a guard to inspect.

The gate runs BEFORE any call to Odoo — including the `fields_get` that
resolves default field names — so a refused tool leaves no trace on the
instance. Nothing here mutates, which is why every handler below reports
`phase="before_mutation"`: a refused read really did change nothing.

`register(mcp)` is called by `server.py`. Importing this module registers
nothing, which keeps every tool callable as a plain function in tests.
"""
import contextlib
import io
import sys
from pathlib import Path

from mcp.server import MCPServer

from odoo_assistant import server
from odoo_assistant.server_errors import (
    ToolExecutionError,
    handle_odoo_exception,
    tool_result,
)
from odoo_assistant.server_safety import gate

# The nine Odoo scripts are flat modules imported by bare name; mirror the
# bootstrap `server.py` uses so this module is importable on its own too.
sys.path.insert(0, str(Path(__file__).parent / "odoo_scripts"))

import query  # noqa: E402  (needs the bootstrap above)
from odoo_client import Odoo  # noqa: E402

MAX_LIMIT = 200
DEFAULT_LIMIT = 80

# The fields `Writer.state_of` reads, plus the name every model carries. Only
# those a model actually has are asked for — see `_default_fields`.
STATE_FIELDS = ("display_name", "name", "state", "payment_state", "amount_residual")

CENSUS_SCRIPT = Path(__file__).parent / "odoo_scripts" / "census.py"

PHONE_NOTE = (
    "\n  Phone: this model has `phone_sanitized`, which Odoo computes itself —\n"
    "  from `mobile` first, `phone` second, one E.164 value per record, not one\n"
    "  per field. It normalises only what it can parse: without a '+' prefix\n"
    "  AND without country_id it stays False, with no error. So write the\n"
    "  number in E.164 yourself (+39...), and put mobiles in `mobile` and\n"
    "  landlines in `phone` — that choice is what decides which one is kept."
)


def _gate_or_raise(model: str, method: str, target: object) -> None:
    """Refuse before Odoo is touched. `target` is the first `execute_kw` slot:
    the domain for a search, the record ids for a read — which is where the
    structural guards look."""
    decision = gate(model, method, target)
    if not decision.allowed:
        raise ToolExecutionError(decision.reason)


def _default_fields(odoo: Odoo, model: str) -> list[str]:
    """The state fields this model actually has.

    Asking for a field a model does not define makes the whole read fail, and
    asking for *all* of them is what once broke a read on `account.move` and
    produced a "presumably draft" report about a posted invoice. An empty list
    means "every field" to Odoo, so the fallback is a name, never nothing.

    `fields_get` answers with whatever crossed the wire, hence the isinstance:
    this is where an untyped response becomes a list of names.
    """
    available = odoo.fields_get(model, list(STATE_FIELDS), ["type"])
    present = available if isinstance(available, dict) else {}
    return [name for name in STATE_FIELDS if name in present] or ["display_name"]


def search_read(
    model: str,
    domain: list,
    fields: list[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    company_ids: list[int] | None = None,
) -> str:
    """Search and read records in one call (Odoo `search_read`).

    Two pitfalls this tool cannot fix for you:

    * `account.move` and `account.move.line` mix customer invoices, vendor
      bills, credit notes and raw journal entries. A domain without
      `move_type` is refused — add `["move_type", "=", "out_invoice"]` (or
      `in_invoice`, `out_refund`, `in_refund`) so the answer matches what the
      user sees on screen.
    * NEVER sum `amount_total`: it is expressed in each record's own currency,
      and eight foreign-currency invoices once inflated a total 11,9×. Ask for
      `amount_total_signed` instead — any field with a `_signed` twin is
      stored in company currency, and the twin is the one to add up.

    Args:
        model: Odoo model, e.g. "sale.order".
        domain: Odoo domain, e.g. [["state", "=", "sale"]].
        fields: Field names to return. Name them: the default asks for every
            field, which is slow and can fail to serialise on wide models.
        limit: Rows to return. Hard-capped at 200.
        offset: Rows to skip — how to page past a truncated result.
        company_ids: Companies to read from, e.g. [1, 2]. On a multi-company
            instance, omitting this reports one company as the whole business.
    """
    _gate_or_raise(model, "search_read", domain)
    kwargs: dict[str, object] = {
        "fields": fields or [],
        "limit": max(1, min(limit, MAX_LIMIT)),
    }
    if offset:
        kwargs["offset"] = offset
    if company_ids:
        kwargs["context"] = {"allowed_company_ids": company_ids}
    try:
        return tool_result(server._get_odoo().call(model, "search_read", [domain], kwargs))
    except Exception as exc:
        return handle_odoo_exception(exc, phase="before_mutation").deliver()


def read_record(model: str, record_id: int, fields: list[str] | None = None) -> str:
    """Read one record by id, always with named fields (writing.md pattern 12).

    Omitting `fields` asks for a short list of state fields — never for all of
    them: that read is slow at best and fails at worst.

    `account.move` and `account.move.line` are refused here, because the
    structural guard wants a `move_type` filter and this tool has nowhere to
    put one. Use `search_read` with
    `[["id", "=", <id>], ["move_type", "=", "out_invoice"]]` instead.

    And never add up `amount_total` across records — it is in the record's own
    currency. `amount_total_signed` is the company-currency twin to sum.

    Args:
        model: Odoo model, e.g. "sale.order".
        record_id: The record's database id.
        fields: Field names to read. Omit for the usual state fields.
    """
    _gate_or_raise(model, "read", [record_id])
    try:
        odoo = server._get_odoo()
        names = fields or _default_fields(odoo, model)
        return tool_result(odoo.call(model, "read", [[record_id], names], {}))
    except Exception as exc:
        return handle_odoo_exception(exc, phase="before_mutation").deliver()


def count_records(
    model: str,
    domain: list | None = None,
    company_ids: list[int] | None = None,
) -> str:
    """Count the records matching a domain (Odoo `search_count`).

    A count is only as honest as its domain:

    * `account.move` / `account.move.line` without a `move_type` filter is
      refused — it would count invoices, bills, credit notes and journal
      entries together and match no figure the user has ever seen.
    * A count answers "how many", never "how much". For an amount, read
      `amount_total_signed` (company currency) and never `amount_total`.
    * On a multi-company instance the count differs per company: pass
      `company_ids` or you are reporting one company as the whole business.

    Args:
        model: Odoo model, e.g. "crm.lead".
        domain: Odoo domain. Omit to count everything the model holds.
        company_ids: Companies to count in, e.g. [1, 2].
    """
    domain = domain or []
    _gate_or_raise(model, "search_count", domain)
    context = {"allowed_company_ids": company_ids} if company_ids else None
    try:
        return tool_result(server._get_odoo().search_count(model, domain, context=context))
    except Exception as exc:
        return handle_odoo_exception(exc, phase="before_mutation").deliver()


def instance_overview() -> str:
    """Summarise the connected instance: version, companies, volumes per area,
    in-house modules, anomalies. This is `query.py` with no arguments.

    It reads the local profile that `census.py` built, so it costs nothing and
    already answers most "how many X" questions — including which areas exist
    at all, so a failing query is never mistaken for a missing module.

    When drilling into these figures, the two rules that keep them meaningful:
    filter `account.move` by `move_type`, and sum `amount_total_signed`, never
    `amount_total`.
    """
    try:
        profile, path = query.load()
    except SystemExit as exc:  # load() explains itself on stderr, then exits
        raise ToolExecutionError(
            f"No instance profile found for this database. Build one with: "
            f"python3 {CENSUS_SCRIPT} --url <base_url> --key <api_key> "
            f"(about a second). It is also what `query.py` reads."
        ) from exc
    # `overview()` prints its report; capturing it is how the verified script is
    # reused verbatim instead of reimplemented, and it keeps the text out of the
    # stdout the JSON-RPC stream owns.
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        query.overview(profile)
    return tool_result(f"{printed.getvalue()}\n(profile: {path})")


def _distribution(odoo: Odoo, model: str, field: str) -> str:
    """How existing records divide over `field`.

    The structural guard refuses an unfiltered `read_group` on the mixed
    models, so a refusal is reported as a refusal — never as "no history".
    """
    decision = gate(model, "read_group", [])
    if not decision.allowed:
        return "not counted (structural guard: this model needs an explicit filter)"
    rows = odoo.call(model, "read_group", [[], [field], [field]], {"lazy": False})
    if not isinstance(rows, list):
        return f"not counted (read_group returned {type(rows).__name__})"
    counted = [
        f"{row.get(field)}={row.get('__count')}"
        for row in rows
        if isinstance(row, dict)
    ]
    return ", ".join(counted) or "no records yet"


def required_fields(model: str) -> str:
    """List the fields Odoo demands before it will accept a `create`, with the
    default it would apply and how existing records actually use it.

    Ask this BEFORE `create_record` on a model you have not written to in this
    session. The answer is read from the live instance — `fields_get` plus
    `default_get` — never from a table in this file, so a model customised
    in-house reports its own requirements.

    The dangerous required field is the one that already carries a default: the
    create succeeds without you naming it and the record lands wherever the
    default points, with no error to notice. `crm.lead.type` is the standing
    example — Odoo defaults it to 'lead', and on an instance that works its
    pipeline as opportunities that record goes straight to a menu nobody opens.
    That is why the live distribution is printed beside each default.

    Args:
        model: Odoo model, e.g. "crm.lead".
    """
    _gate_or_raise(model, "fields_get", [])
    try:
        odoo = server._get_odoo()
        meta = odoo.fields_get(model, [], ["string", "type", "required", "selection"])
        if not isinstance(meta, dict):
            raise ToolExecutionError(f"{model}: fields_get returned {type(meta).__name__}")
        required = {
            name: spec
            for name, spec in meta.items()
            if isinstance(spec, dict) and spec.get("required")
        }
        answered = odoo.call(model, "default_get", [sorted(required)], {})
        defaults = answered if isinstance(answered, dict) else {}

        lines = [f"{model} — Odoo requires {len(required)} field(s) for a create:"]
        for name, spec in sorted(required.items()):
            lines.append(f"  {name}  ({spec.get('type')})  {spec.get('string')!r}")
            selection = spec.get("selection")
            if isinstance(selection, list):
                allowed = " | ".join(
                    str(pair[0])
                    for pair in selection
                    if isinstance(pair, (list, tuple)) and pair
                )
                lines.append(f"      one of: {allowed}")
            if name in defaults:
                lines.append(
                    f"      Odoo would default to {defaults[name]!r} — "
                    f"existing records: {_distribution(odoo, model, name)}"
                )
        if "phone_sanitized" in meta:
            lines.append(PHONE_NOTE)
        return tool_result("\n".join(lines))
    except ToolExecutionError:
        raise
    except Exception as exc:
        return handle_odoo_exception(exc, phase="before_mutation").deliver()


def register(mcp: MCPServer) -> None:
    """Attach the read tools to `mcp`. Called by server.py, never at import."""
    for tool in (
        search_read,
        read_record,
        count_records,
        instance_overview,
        required_fields,
    ):
        mcp.tool()(tool)
