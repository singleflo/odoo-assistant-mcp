#!/usr/bin/env python3
"""Tool result formatting and the exception -> isError mapping.

**How isError works in SDK 2.0** (read from `mcp/server/mcpserver/server.py`
lines 415-424, not assumed): `_handle_call_tool` re-raises `MCPError` — the
only class carrying a numeric JSON-RPC code — and converts *every other*
exception into `CallToolResult(content=[TextContent(text=str(e))],
is_error=True)`. A tool that returns normally yields `is_error=False`.

So the entire mechanism is: **raise a plain exception for an error, return a
string for a success.** No custom JSON-RPC codes exist here, by construction —
`MCPError` is never raised. `ToolOutcome.deliver()` is the single place that
encoding lives, so a tool function reads:

    try:
        rows = odoo.search_read(...)
    except Exception as exc:
        return handle_odoo_exception(exc, lambda: writer.state_of(model, ids)).deliver()
    return tool_result(rows)

**Why an exception can be a success**: Odoo commits before serialising its
reply, so `OdooExecutedButUnserializable` means the write IS applied. Reporting
it as a failure invites a retry, and a retry posts the payment twice. Nothing
in this module ever retries; the only recovery is a re-read, and a state that
was not re-read is never named (references/SKILL.md rule 5).
"""
import json
import sys
from pathlib import Path
from typing import Callable, NamedTuple

# The nine Odoo scripts are flat modules imported by bare name; mirror the
# bootstrap `server.py` uses so this module is importable on its own too.
sys.path.insert(0, str(Path(__file__).parent / "odoo_scripts"))

from odoo_client import (  # noqa: E402  (needs the bootstrap above)
    MissingCredentials,
    OdooError,
    OdooExecutedButUnserializable,
    ProductionWriteBlocked,
)

MAX_RESULT_CHARS = 5000
TRUNCATION_NOTICE = "\n... truncated, use limit/offset to narrow the result."


class ToolExecutionError(RuntimeError):
    """Raised by `ToolOutcome.deliver()` so the SDK returns isError:true.

    Deliberately NOT an `MCPError`: that one would become a JSON-RPC protocol
    error with a numeric code, which the model never sees as tool output.
    Its `str()` is the whole message the client receives.
    """


class ToolOutcome(NamedTuple):
    """What a tool should report: `text`, and whether it is a failure."""

    is_error: bool
    text: str

    def deliver(self) -> str:
        """Return the text, or raise so the SDK marks the result isError:true."""
        if self.is_error:
            raise ToolExecutionError(self.text)
        return self.text


def tool_result(payload: object) -> str:
    """Serialise `payload` and cap it at MAX_RESULT_CHARS.

    An uncapped result does not merely produce a long answer — it evicts the
    conversation that asked for it. When the cap bites, the notice names the
    remedy the caller can actually apply.
    """
    text = payload if isinstance(payload, str) else json.dumps(
        payload, default=str, ensure_ascii=False)
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return text[:MAX_RESULT_CHARS] + TRUNCATION_NOTICE


def handle_odoo_exception(
    exc: BaseException,
    reread_state_fn: Callable[[], object] | None = None,
) -> ToolOutcome:
    """Map an exception to what the tool should report. Never retries.

    `reread_state_fn` is used ONLY for the committed-but-unserializable case,
    and only once — typically `lambda: writer.state_of(model, ids)`.
    """
    match exc:
        case OdooExecutedButUnserializable():
            return ToolOutcome(False, _committed(exc, reread_state_fn))
        case MissingCredentials() | ProductionWriteBlocked():
            return ToolOutcome(
                True,
                f"{exc}\nNo call was made: nothing was sent to Odoo, so no "
                f"record changed. Fix the configuration and call the tool again.",
            )
        case OdooError():
            return ToolOutcome(
                True,
                f"{exc}\nOdoo refused the call, so nothing changed. Fix the "
                f"arguments named above — repeating the same call cannot succeed.",
            )
        case _:
            return ToolOutcome(
                True,
                f"UNCERTAIN: {exc}\nThe call may or may not have been applied "
                f"before this failure. Re-read the record and decide from its "
                f"actual state; do NOT repeat the call blindly.",
            )


def _committed(
    exc: OdooExecutedButUnserializable,
    reread_state_fn: Callable[[], object] | None,
) -> str:
    """Text for a write that landed but could not report itself."""
    if reread_state_fn is None:
        state = "NOT RE-READ — no read was performed, so the state is unknown."
    else:
        try:
            state = repr(reread_state_fn())
        except Exception as read_exc:  # a failed read proves nothing
            state = f"RE-READ FAILED ({read_exc}) — the state is unknown."
    return (
        f"COMMITTED but result unserializable. {exc}\n"
        f"Verified state: {state}\n"
        f"Do NOT retry — the change is already applied; a second call would "
        f"duplicate it."
    )
