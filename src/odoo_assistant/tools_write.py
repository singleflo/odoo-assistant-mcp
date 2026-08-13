#!/usr/bin/env python3
"""The four write tools: create a record, write to one, run an action, cancel.

No level is written down here. `gate()` classifies the ACTUAL method string at
call time, so `run_action` is a state change when it confirms an order and
destructive when it cancels one — a level attached to the tool would be a lie
in one of those two cases.

Every tool keeps the same three beats:

    gate FIRST, before a client exists   a refusal costs zero Odoo calls
    then the Writer, never the raw call  before/after, wizard-follow, unique_on
    report the Writer's own verdict      "NO CHANGE" is an answer, not a failure

Nothing retries. Odoo commits before serialising its reply, so an exception can
mean the write landed; the only recovery is one re-read, which
`handle_odoo_exception` performs and this module never fakes.

The annotations in `register()` are for the host's UI. They are documentation,
not enforcement — a host may ignore them, which is why `gate()` runs inside
every function body instead of being implied by a hint.
"""
import sys
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

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

from write_patterns import Writer  # noqa: E402  (needs the bootstrap above)


def _writer() -> Writer:
    """A Writer over the shared client.

    `server._get_odoo` is read from the module at call time rather than
    imported by value — that indirection is what keeps the client injectable.
    """
    return Writer(server._get_odoo())


def _guard(model: str, method: str, ids: Any = None, values: Any = None) -> None:
    """Refuse before anything reaches Odoo. Raising is what marks isError.

    The refusal is the gate's own text: it already names the level, the current
    ceiling and the `ODOO_MCP_MAX_LEVEL` value that would allow the call. A
    second explanation written here would drift from the one under test.
    """
    decision = gate(model, method, ids, values)
    if not decision.allowed:
        raise ToolExecutionError(decision.reason)


def create_record(
    model: str,
    values: dict[str, Any],
    unique_on: list[str] | None = None,
) -> str:
    """Create a record, reusing an existing match when `unique_on` is given.

    `unique_on` is a list of FIELD NAMES taken from `values` (e.g.
    `["name", "email"]`): they are searched first and the existing id comes
    back instead of a duplicate. Odoo has no idempotency key, so a create that
    is retried is simply a second record — this is the only protection there
    is, and a cold-start run without it produced four identical customers.

    Multi-company: put `company_id` in `values`. The context decides what is
    visible, not which company owns the new record.
    """
    _guard(model, "create", None, values)
    writer = _writer()
    try:
        record_id = writer.create(model, values, unique_on=unique_on)
    except Exception as exc:
        return handle_odoo_exception(exc).deliver()
    return tool_result(f"Created (or reused) {model} id={record_id}")


def write_record(model: str, record_id: int, values: dict[str, Any]) -> str:
    """Write field values to one record and report what actually changed.

    Writing the value a record already holds succeeds and changes nothing; only
    the before/after comparison tells that apart from a real update, so that
    comparison is the answer.

    Setting `active` to False archives the record — the same visible outcome as
    deleting it — and is classified destructive rather than as a plain write.
    """
    _guard(model, "write", record_id, values)
    writer = _writer()
    try:
        result = writer.write(model, record_id, values)
    except Exception as exc:
        return handle_odoo_exception(
            exc, lambda: writer.state_of(model, record_id, list(values))
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


def run_action(model: str, method: str, record_ids: list[int]) -> str:
    """Run a workflow method and report the state it left behind.

    The level follows `method`: confirming or posting is a state change,
    cancelling or unlinking is destructive and refused unless the server's
    ceiling was raised deliberately.

    Two behaviours come from the Writer and are worth knowing: a returned dict
    carrying `res_model` is a wizard to follow rather than a result, and a
    transition is one-way — calling it twice raises instead of doing nothing.
    """
    _guard(model, method, record_ids)
    writer = _writer()
    try:
        result = writer.act(model, method, record_ids, watch="state")
    except Exception as exc:
        return handle_odoo_exception(
            exc, lambda: writer.state_of(model, record_ids)
        ).deliver()
    return tool_result(repr(result))


def cancel_record(model: str, record_id: int) -> str:
    """Cancel a record through `action_cancel`, following the wizard it returns.

    Destructive, so the default ceiling refuses it and says what would not.
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

    mcp.add_tool(create_record, annotations=additive)
    mcp.add_tool(write_record, annotations=overwriting)
    mcp.add_tool(run_action, annotations=transition)
    mcp.add_tool(cancel_record, annotations=transition)
