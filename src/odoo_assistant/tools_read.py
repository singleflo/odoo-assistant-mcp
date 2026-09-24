#!/usr/bin/env python3
"""The four read tools (PRD §5B): search_read, read_record, count_records,
instance_overview.

The three that query Odoo — `search_read`, `read_record`, `count_records` — all
go through `gate()`, though reads are never subject to the allow/deny lists:
what applies to them are the structural guards `gate()` runs alongside them.
`account.move` holds customer invoices, vendor bills, credit notes AND raw
journal entries in one table, so a query without `move_type` answers a
question nobody asked (3.613 records where the user sees 373). One code path
per tool means that guard cannot be skipped.

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
import json
import sys
from pathlib import Path

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field
from typing_extensions import Annotated

from odoo_assistant import paths, server
from odoo_assistant.server_errors import (
    ToolExecutionError,
    handle_odoo_exception,
    tool_result,
)
from odoo_assistant.server_safety import gate

# The nine Odoo scripts are flat modules imported by bare name; mirror the
# bootstrap `server.py` uses so this module is importable on its own too.
sys.path.insert(0, str(Path(__file__).parent / "odoo_scripts"))

# Package-qualified on purpose: `_redirect_profiles()` reassigns a module global
# on both, and the patch is only visible to other importers if everyone reaches
# the same module object. Same reasoning as `explore_module.REF_DIR` in
# tools_evolution.py.
from odoo_assistant.odoo_scripts import census, query  # noqa: E402
from odoo_client import Odoo  # noqa: E402

MAX_LIMIT = 200
DEFAULT_LIMIT = 80

# One window of a long text field. Small enough that the window plus its header
# always fits `MAX_RESULT_CHARS`, so walking a field never hits the cap that
# made the field unreadable in the first place.
LONG_FIELD_WINDOW = 4000

# The fields `Writer.state_of` reads, plus the name every model carries. Only
# those a model actually has are asked for — see `_default_fields`.
STATE_FIELDS = ("display_name", "name", "state", "payment_state", "amount_residual")

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
    model: Annotated[str, Field(description=(
        'Odoo model, e.g. "sale.order". A model customised in-house reports '
        "its own fields, read live."))],
    domain: Annotated[list, Field(description=(
        'Odoo domain, e.g. [["state", "=", "sale"]]. account.move and '
        "account.move.line without a move_type filter are refused."))],
    fields: Annotated[list[str] | None, Field(description=(
        "Field names to return. The default asks for every field — slow, and "
        "it can fail to serialise on wide models. Name them."))] = None,
    limit: Annotated[int, Field(description=(
        "Rows to return, hard-capped at 200."))] = DEFAULT_LIMIT,
    offset: Annotated[int, Field(description=(
        "Rows to skip — how to page past a truncated result."))] = 0,
    company_ids: Annotated[list[int] | None, Field(description=(
        "Companies to read from, e.g. [1, 2]. Omitting this on a "
        "multi-company instance reports one company as the whole business."))] = None,
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

    One line before you call: rows you only intend to count are a wasted read
    — `count_records` answers totals, `group_records` answers totals per
    value, and neither moves records through the context window. And when a
    SINGLE field is larger than the 5000-character cap, paging with `offset`
    returns the same cut text forever: `read_long_field` walks that one field
    in windows instead.

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


def read_record(
    model: Annotated[str, Field(description=(
        'Odoo model, e.g. "sale.order". account.move and account.move.line '
        "are refused here — the structural guard has nowhere to put a "
        "move_type filter on a single id."))],
    record_id: Annotated[int, Field(description="The record's database id.")],
    fields: Annotated[list[str] | None, Field(description=(
        "Field names to read. Omit for the usual state fields — never read "
        "every field of a wide model."))] = None,
) -> str:
    """Read one record by id, always with named fields (writing.md pattern 12).

    Omitting `fields` asks for a short list of state fields — never for all of
    them: that read is slow at best and fails at worst.

    `account.move` and `account.move.line` are refused here, because the
    structural guard wants a `move_type` filter and this tool has nowhere to
    put one. Use `search_read` with
    `[["id", "=", <id>], ["move_type", "=", "out_invoice"]]` instead.

    And never add up `amount_total` across records — it is in the record's own
    currency. `amount_total_signed` is the company-currency twin to sum.

    The answer is capped at 5000 characters. When ONE field is bigger than
    that on its own — a description holding a transcript, a long `body` —
    asking for fewer fields cannot help: use `read_long_field`, which walks a
    single field in windows to its end.

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


def read_long_field(
    model: Annotated[str, Field(description=(
        'Odoo model, e.g. "crm.lead". account.move and account.move.line are '
        "refused here for the same reason read_record refuses them."))],
    record_id: Annotated[int, Field(description="The record's database id.")],
    field: Annotated[str, Field(description=(
        'The single text field to walk, e.g. "description" or "body". '
        "`describe_model` lists what a model has."))],
    offset: Annotated[int, Field(description=(
        "Character to start from. 0 is the beginning; each answer reports the "
        "offset of the next window and the total length."))] = 0,
) -> str:
    """Read ONE long text field in windows, so a value larger than the 5000-char
    result cap stays readable to the end.

    Every tool result is capped at 5000 characters. When a single value is
    bigger than that — a `description` holding a pasted transcript, an email
    `body` — narrowing the domain or the field list cannot help: that one value
    already IS the whole answer, so the tail is unreachable and the caller ends
    up asking for the same cut text again. Measured on a live lead: its
    description came back cut, and no limit or offset could ever reach the
    rest of it.

    This reads that field alone and returns a 4000-character window of it,
    naming the total length and the offset of the next window, so the value can
    be walked to the end one call at a time. For anything else — several
    fields, several records — `read_record` and `search_read` stay the tools.

    Args:
        model: Odoo model, e.g. "crm.lead".
        record_id: The record's database id.
        field: The single text field to walk, e.g. "description".
        offset: Character to start from. 0 is the beginning.
    """
    _gate_or_raise(model, "read", [record_id])
    try:
        odoo = server._get_odoo()
        rows = odoo.call(model, "read", [[record_id], [field]], {})
    except Exception as exc:
        return handle_odoo_exception(exc, phase="before_mutation").deliver()

    row = rows[0] if isinstance(rows, list) and rows else {}
    if not isinstance(row, dict) or field not in row:
        raise ToolExecutionError(
            f"{model} id={record_id} answered without {field!r}. Either the "
            f"record does not exist or the model has no such field — "
            f"`describe_model` lists the fields it does have.")

    value = row[field]
    # Odoo returns False, not "", for an empty text field.
    if value is False or value is None or value == "":
        return tool_result(f"{model} id={record_id}: {field} is empty.")

    text = value if isinstance(value, str) else str(value)
    total = len(text)
    start = max(0, min(offset, total))
    window = text[start:start + LONG_FIELD_WINDOW]
    end = start + len(window)
    next_step = (
        f"Next window: offset={end}." if end < total
        else "This is the end of the field.")
    return tool_result(
        f"{model} id={record_id} {field} — characters {start}-{end} of "
        f"{total}. {next_step}\n{window}")


def count_records(
    model: Annotated[str, Field(description=(
        'Odoo model, e.g. "crm.lead". A model customised in-house reports '
        "its own fields, read live."))],
    domain: Annotated[list | None, Field(description=(
        'Odoo domain. Omit to count everything the model holds. '
        "account.move and account.move.line without a move_type filter are "
        "refused."))] = None,
    company_ids: Annotated[list[int] | None, Field(description=(
        "Companies to count in, e.g. [1, 2]. Omitting this on a "
        "multi-company instance reports one company as the whole business."))] = None,
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

    One value per call is all this gives. For a breakdown — how many per
    state, per stage, per month — use `group_records`, which returns every
    bucket's count in one call instead of one call per bucket.

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


# The models where `amount_total` is per-record currency. Elsewhere (sale.order
# and friends) the total already sits in the company currency, and summing it
# is legitimate — the refusal must not outlive the landmine that justifies it.
GROUPED_TOTALS = frozenset({"account.move", "account.move.line"})


def group_records(
    model: Annotated[str, Field(description=(
        'Odoo model, e.g. "crm.lead". A model customised in-house reports '
        "its own fields, read live."))],
    domain: Annotated[list, Field(description=(
        'Odoo domain, e.g. [["state", "=", "sale"]] or [["active", "in", '
        "[true, false]] to include archived records."))],
    group_by: Annotated[list[str], Field(description=(
        'One or two fields, e.g. ["stage_id"] or ["create_date:month", '
        '"stage_id"]. Date granularity: day, week, month, quarter, year.'))],
    aggregate: Annotated[list[str] | None, Field(description=(
        'Optional specs like ["amount_total_signed:sum"] — also :avg, :min, '
        ":max. Never amount_total on account.move / account.move.line: "
        "per-record currency, sum the _signed twin instead."))] = None,
    company_ids: Annotated[list[int] | None, Field(description=(
        "Companies to read from, e.g. [1, 2]. Omitting this on a "
        "multi-company instance reports one company as the whole business."))] = None,
) -> str:
    """Group records and count (or aggregate) per bucket (Odoo `read_group`).

    The tool for "how many per state / stage / month": one call returns every
    existing bucket with its count. Issuing one `count_records` per value is
    the pattern this tool exists to prevent — N round trips where one answers,
    and on a live instance half of them come back zero.

    Args:
        model: Odoo model, e.g. "crm.lead".
        domain: Odoo domain, e.g. [["state", "=", "sale"]].
        group_by: one or two fields, e.g. ["stage_id"] or
            ["create_date:month", "stage_id"]. Date granularity: day, week,
            month, quarter, year.
        aggregate: optional specs like ["amount_total_signed:sum"] — also
            :avg, :min, :max. On `account.move` / `account.move.line`,
            `amount_total` is refused: it is expressed in each record's own
            currency, and summing it once inflated a total 11,9×. Aggregate
            `amount_total_signed`, the company-currency twin, instead.
        company_ids: Companies to read from, e.g. [1, 2]. On a multi-company
            instance, omitting this reports one company as the whole business.
    """
    if not group_by or len(group_by) > 2:
        raise ToolExecutionError(
            'group_by takes one or two fields, e.g. ["stage_id"] or '
            '["create_date:month", "stage_id"] — grouping by nothing is '
            "count_records' job.")
    if model in GROUPED_TOTALS and aggregate:
        refused = [a for a in aggregate if a.split(":")[0] == "amount_total"]
        if refused:
            raise ToolExecutionError(
                "amount_total is stored in each record's own currency — "
                "summing it mixes currencies (measured: 11,9× off). Aggregate "
                "amount_total_signed, the company-currency twin, instead.")
    _gate_or_raise(model, "read_group", domain)
    kwargs: dict[str, object] = {"lazy": False}
    if company_ids:
        kwargs["context"] = {"allowed_company_ids": company_ids}
    try:
        rows = server._get_odoo().call(
            model, "read_group", [domain, aggregate or [], group_by], kwargs)
    except Exception as exc:
        return handle_odoo_exception(exc, phase="before_mutation").deliver()
    # lazy=False is what makes the rows flat: the lazy default nests and would
    # need one follow-up call per bucket, which is the round-trip pattern this
    # tool exists to end. Odoo decorates every row with __domain (the whole
    # query repeated), __range and __fold — none of that is the answer, and
    # together it eats most of a 5000-char cap. The count moves under "count":
    # its wire spelling changes between modes, and the model reading this never
    # asked for Odoo's spelling.
    cleaned = []
    for row in rows if isinstance(rows, list) else []:
        count = row.get("__count")
        clean = {k: v for k, v in row.items() if not k.startswith("__")}
        if count is not None:
            clean["count"] = count
        cleaned.append(clean)
    return tool_result(cleaned)


def _redirect_profiles() -> str:
    """Point the instance-profile cache at a writable per-user directory.

    Two variables for one folder gave three resolutions of one path: the
    server override, the scripts' own default, and `paths.data_dir()`. The
    scripts' default already resolves to the same
    `.../odoo-assistant/instances`, so one data directory keeps the MCP server
    and its scripts in agreement while letting `ODOO_MCP_DATA_DIR` move
    everything at once.
    """
    target = str(paths.data_dir() / "instances")
    census.PROFILE_DIR = target
    query.PROFILE_DIR = target
    return target


def instance_overview(
    refresh: Annotated[bool, Field(description=(
        "Rebuild the profile from the connected instance instead of reusing "
        "the cached one. Pass True after the instance has changed."))] = False,
) -> str:
    """Summarise the connected instance: version, companies, volumes per area,
    in-house modules, anomalies.

    The profile is built from the instance this server is CONNECTED to, and
    cached per instance — `census.profile_path()` keys it on the live client,
    not on an environment variable. That distinction is the whole point: with
    two instances profiled on one machine, choosing by `ODOO_DB` (which is
    discovered now, so often unset) once fell through to "the first file on
    disk" and reported a neighbour's numbers as this instance's, with no error
    and a perfectly plausible report.

    First call against a new instance builds the profile, which costs a second
    or so; every later call is free. Pass `refresh=True` after the instance has
    changed — the report carries the timestamp it was taken.

    When drilling into these figures, the two rules that keep them meaningful:
    filter `account.move` by `move_type`, and sum `amount_total_signed`, never
    `amount_total`.

    Args:
        refresh: rebuild the profile from the instance instead of reusing it.
    """
    try:
        odoo = server._get_odoo()
        path = Path(census.profile_path(odoo))
        if refresh or not path.exists():
            profile = census.census(odoo)
            path.write_text(json.dumps(profile), encoding="utf-8")
        else:
            profile = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — census only reads
        return handle_odoo_exception(exc, phase="before_mutation").deliver()
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


def required_fields(
    model: Annotated[str, Field(description=(
        'Odoo model, e.g. "crm.lead". Read live from the instance, so a '
        "model customised in-house reports its own requirements."))],
) -> str:
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


def describe_model(
    model: Annotated[str, Field(description=(
        'Odoo model, e.g. "crm.lead". A model customised in-house reports '
        "its own fields, read live."))],
) -> str:
    """List a model's fields as the live instance defines them (Odoo `fields_get`).

    Answers "what can I filter, group or write on this model" in one call —
    the alternative, reading `ir.model.fields` through `search_read`, takes
    one call per batch of names and returns rows you then have to join.

    Required fields are starred. For what a `create` demands — including the
    default Odoo would apply and how existing records actually use it — use
    `required_fields`, which reads deeper on exactly that question.

    Args:
        model: Odoo model, e.g. "crm.lead".
    """
    _gate_or_raise(model, "fields_get", [])
    try:
        odoo = server._get_odoo()
        meta = odoo.fields_get(
            model, [], ["string", "type", "required", "selection", "relation"])
        if not isinstance(meta, dict):
            raise ToolExecutionError(f"{model}: fields_get returned {type(meta).__name__}")
        lines = [f"{model} — {len(meta)} fields (* = required):"]
        for name in sorted(meta):
            spec = meta[name] if isinstance(meta[name], dict) else {}
            line = f"  {name}{'*' if spec.get('required') else ''}  {spec.get('type', '?')}"
            if spec.get("relation"):
                line += f" → {spec['relation']}"
            selection = spec.get("selection")
            if isinstance(selection, list) and selection:
                values = [
                    str(pair[0]) for pair in selection
                    if isinstance(pair, (list, tuple)) and pair
                ]
                if values:
                    line += f"  [{' | '.join(values)}]"
            label = spec.get("string")
            if label:
                line += f"  ({label})"
            lines.append(line)
        return tool_result("\n".join(lines))
    except ToolExecutionError:
        raise
    except Exception as exc:
        return handle_odoo_exception(exc, phase="before_mutation").deliver()


def register(mcp: MCPServer) -> None:
    """Attach the read tools to `mcp`. Called by server.py, never at import."""
    _redirect_profiles()
    # All pure reads: nothing they run can change data, every repeat of the
    # call lands in the same place, and the answers come from an open-ended
    # external system. The title lives INSIDE the annotations: Anthropic's
    # directory reads annotations.title and flags its absence per tool,
    # while Tool.title serves the hosts that read the tool field instead.
    def _read(title: str) -> ToolAnnotations:
        return ToolAnnotations(
            title=title, read_only_hint=True, destructive_hint=False,
            idempotent_hint=True, open_world_hint=True)

    mcp.add_tool(
        search_read, title="Search records", annotations=_read("Search records"))
    mcp.add_tool(
        read_record, title="Read a record", annotations=_read("Read a record"))
    mcp.add_tool(
        read_long_field, title="Read a long text field",
        annotations=_read("Read a long text field"))
    mcp.add_tool(
        count_records, title="Count records", annotations=_read("Count records"))
    mcp.add_tool(
        group_records, title="Group records", annotations=_read("Group records"))
    mcp.add_tool(
        instance_overview, title="Instance overview",
        annotations=_read("Instance overview"))
    mcp.add_tool(
        required_fields, title="Required fields for create",
        annotations=_read("Required fields for create"))
    mcp.add_tool(
        describe_model, title="Describe a model",
        annotations=_read("Describe a model"))


_redirect_profiles()
