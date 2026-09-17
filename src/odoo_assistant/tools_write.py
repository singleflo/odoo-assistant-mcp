#!/usr/bin/env python3
"""The four write tools: create a record, write to one, run an action, cancel.

No verdict is written down here. `gate()` judges the ACTUAL method string at
call time against the operator's allow/deny lists, so `run_action` is a plain
state change when it confirms an order and a denied name when it cancels one —
a verdict attached to the tool would be a lie in one of those two cases.

Every tool keeps the same three beats:

    gate FIRST, before a client exists   a refusal costs zero Odoo calls
    then the Writer, never the raw call  before/after, wizard-follow, unique_on
    report the Writer's own verdict      "NO CHANGE" is an answer, not a failure

Nothing retries. Odoo commits before serialising its reply, so an exception can
mean the write landed; the only recovery is one re-read, which
`handle_odoo_exception` performs and this module never fakes. Every handler here
says `phase="after_mutation_possible"`, because `_guard` runs BEFORE the `try`:
whatever reaches those blocks failed with the write already in flight, and only
a re-read may say where it landed.

The annotations in `register()` are for the host's UI. They are documentation,
not enforcement — a host may ignore them, which is why `gate()` runs inside
every function body instead of being implied by a hint.
"""
import sys
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field
from typing_extensions import Annotated

from odoo_assistant import server
from odoo_assistant.server_errors import (
    ToolExecutionError,
    ToolOutcome,
    handle_odoo_exception,
    tool_result,
)
from odoo_assistant.server_safety import gate

# The nine Odoo scripts are flat modules imported by bare name; mirror the
# bootstrap `server.py` uses so this module is importable on its own too.
sys.path.insert(0, str(Path(__file__).parent / "odoo_scripts"))

from write_patterns import Writer  # noqa: E402  (needs the bootstrap above)


def _writer() -> Writer:
    """A Writer over the shared client.

    `server._get_odoo` is read from the module at call time rather than
    imported by value — that indirection is what keeps the client injectable.
    """
    return Writer(server._get_odoo())


def _guard(model: str, method: str, ids: Any = None, values: Any = None) -> None:
    """Refuse before anything reaches Odoo. Raising is what marks isError.

    The refusal is the gate's own text: it names the ODOO_MCP_DENY entry (or
    ODOO_MCP_ALLOW_UNLINK, or ODOO_MCP_ALLOW) that decided. A second
    explanation written here would drift from the one under test.
    """
    decision = gate(model, method, ids, values)
    if not decision.allowed:
        raise ToolExecutionError(decision.reason)


def create_record(
    model: Annotated[str, Field(description=(
        'Odoo model, e.g. "sale.order". Call `required_fields` first on a '
        "model you have not written to: a required field that carries a "
        "default lands the record where the default points."))],
    values: Annotated[dict[str, Any], Field(description=(
        "Field values for the new record — field names to values. "
        "Multi-company: put company_id in here; the context decides what is "
        "visible, not which company owns the record."))],
    unique_on: Annotated[list[str] | None, Field(description=(
        'Field names searched first, e.g. ["email"]: an existing match comes '
        "back instead of a duplicate. Odoo has no idempotency key — this is "
        "the only protection a retried create has."))] = None,
) -> str:
    """Create a record, reusing an existing match when `unique_on` is given.

    `unique_on` is a list of FIELD NAMES taken from `values` (e.g.
    `["name", "email"]`): they are searched first and the existing id comes
    back instead of a duplicate. Odoo has no idempotency key, so a create that
    is retried is simply a second record — this is the only protection there
    is, and a cold-start run without it produced four identical customers.

    Multi-company: put `company_id` in `values`. The context decides what is
    visible, not which company owns the new record.

    When the gate refuses, tell the user which entry of ODOO_MCP_DENY (or ODOO_MCP_ALLOW_UNLINK) would allow it and stop; never retry with another method name.
    """
    _guard(model, "create", None, values)
    try:
        writer = _writer()
        record_id = writer.create(model, values, unique_on=unique_on)
    except Exception as exc:
        # No re-read: `Writer.create` raises without ever handing back an id, and
        # inventing one to read would name a record nobody looked at.
        return handle_odoo_exception(exc, phase="after_mutation_possible").deliver()
    return tool_result(f"Created (or reused) {model} id={record_id}")


def write_record(
    model: Annotated[str, Field(description=(
        'Odoo model, e.g. "sale.order". A write is done only when a re-read '
        "proves it — the tool reports before/after itself."))],
    record_id: Annotated[int, Field(description="The record's database id.")],
    values: Annotated[dict[str, Any], Field(description=(
        "Field values to write — field names to values. Writing a value the "
        "record already holds changes nothing, and the report says so."))],
) -> str:
    """Write field values to one record and report what actually changed.

    Writing the value a record already holds succeeds and changes nothing; only
    the before/after comparison tells that apart from a real update, so that
    comparison is the answer.

    Setting `active` to False archives the record — the same visible outcome as
    deleting it — so the gate matches it as `archive`, which the default
    ODOO_MCP_DENY list refuses.

    When the gate refuses, tell the user which entry of ODOO_MCP_DENY (or ODOO_MCP_ALLOW_UNLINK) would allow it and stop; never retry with another method name.
    """
    _guard(model, "write", record_id, values)
    try:
        writer = _writer()
    except Exception as exc:
        return handle_odoo_exception(
            exc, phase="after_mutation_possible"
        ).deliver()
    try:
        result = writer.write(model, record_id, values)
    except Exception as exc:
        return handle_odoo_exception(
            exc, lambda: writer.state_of(model, record_id, list(values)),
            phase="after_mutation_possible",
        ).deliver()
    if result.raised:
        # `result.after` is `None` both when the field genuinely reads None AND
        # when Writer._read() swallowed an OdooError during its post-write
        # verification. We cannot distinguish the two from here, so we report
        # the value Writer observed without overclaiming it as "verified".
        state = (
            repr(result.after) if result.after is not None
            else "unknown (the post-write re-read returned no value)"
        )
        return ToolOutcome(
            False,
            f"COMMITTED but result unserializable. {result.raised}\n"
            f"Post-write observation: {state}\n"
            "Do NOT retry — the change is already applied.",
        ).deliver()
    if not result.changed:
        return tool_result(
            f"NOT CHANGED — {model} id={record_id} {result.watch} was already "
            f"{result.before!r}. The write ran and changed nothing."
        )
    return tool_result(
        f"{model} id={record_id} {result.watch}: "
        f"before: {result.before!r} -> after: {result.after!r}"
    )


def run_action(
    model: Annotated[str, Field(description=(
        'Odoo model, e.g. "sale.order". Unlink is decided before the '
        "allow/deny lists and only ODOO_MCP_ALLOW_UNLINK grants it."))],
    method: Annotated[str, Field(description=(
        'Workflow method name, e.g. "action_post" or "action_confirm". '
        "Judged by exact name against the operator's allow/deny lists; "
        "methods starting with _ are always refused."))],
    record_ids: Annotated[list[int], Field(description=(
        "The records to run it on. A transition is one-way — calling it "
        "twice raises instead of doing nothing."))],
) -> str:
    """Run a workflow method and report the state it left behind.

    The gate follows `method`: a name in ODOO_MCP_DENY — the default list
    refuses `action_cancel` and friends — or `unlink` without
    ODOO_MCP_ALLOW_UNLINK=yes, never reaches Odoo.

    Two behaviours come from the Writer and are worth knowing: a returned dict
    carrying `res_model` is a wizard to follow rather than a result, and a
    transition is one-way — calling it twice raises instead of doing nothing.

    When the gate refuses, tell the user which entry of ODOO_MCP_DENY (or ODOO_MCP_ALLOW_UNLINK) would allow it and stop; never retry with another method name.
    """
    _guard(model, method, record_ids)
    try:
        writer = _writer()
    except Exception as exc:
        return handle_odoo_exception(
            exc, phase="after_mutation_possible"
        ).deliver()
    try:
        result = writer.act(model, method, record_ids, watch="state")
    except Exception as exc:
        return handle_odoo_exception(
            exc, lambda: writer.state_of(model, record_ids),
            phase="after_mutation_possible",
        ).deliver()
    if result.raised:
        # See write_record for why we do not call this "verified": result.after
        # is None both when the field genuinely reads None and when
        # Writer._read() swallowed an OdooError on its post-write verification.
        state = (
            repr(result.after) if result.after is not None
            else "unknown (the post-write re-read returned no value)"
        )
        return ToolOutcome(
            False,
            f"COMMITTED but result unserializable. {result.raised}\n"
            f"Post-write observation: {state}\n"
            "Do NOT retry — the change is already applied.",
        ).deliver()
    return tool_result(repr(result))


def cancel_record(
    model: Annotated[str, Field(description=(
        'Odoo model, e.g. "sale.order". action_cancel sits in the default '
        "ODOO_MCP_DENY list, so this is refused until the operator removes "
        "that entry."))],
    record_id: Annotated[int, Field(description="The record's database id.")],
) -> str:
    """Cancel a record through `action_cancel`, following the wizard it returns.

    `action_cancel` sits in the default ODOO_MCP_DENY list, so this tool is
    refused until the operator removes that entry from the variable.
    """
    return run_action(model, "action_cancel", [record_id])


def register(mcp: MCPServer) -> None:
    """Publish the four write tools.

    `create_record` only adds, and repeats only when `unique_on` is left unset.
    `write_record` overwrites values, but writing the same ones twice lands in
    the same place. Actions transition state one way, so nothing about them is
    idempotent. All four reach an external system, hence the open world.
    """
    additive = ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=False,
        open_world_hint=True)
    overwriting = ToolAnnotations(
        read_only_hint=False, destructive_hint=True, idempotent_hint=True,
        open_world_hint=True)
    transition = ToolAnnotations(
        read_only_hint=False, destructive_hint=True, idempotent_hint=False,
        open_world_hint=True)

    mcp.add_tool(create_record, title="Create a record", annotations=additive)
    mcp.add_tool(write_record, title="Update a record", annotations=overwriting)
    mcp.add_tool(run_action, title="Run a workflow action", annotations=transition)
    mcp.add_tool(cancel_record, title="Cancel a record", annotations=transition)
