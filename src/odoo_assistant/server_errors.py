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
        return handle_odoo_exception(
            exc, lambda: writer.state_of(model, ids), phase="before_mutation"
        ).deliver()
    return tool_result(rows)

**Why an exception can be a success**: Odoo commits before serialising its
reply, so `OdooExecutedButUnserializable` means the write IS applied. Reporting
it as a failure invites a retry, and a retry posts the payment twice. Nothing
in this module ever retries; the only recovery is a re-read, and a state that
was not re-read is never named (references/SKILL.md rule 5).

**Why the same exception needs the PHASE to be read correctly**: `OdooError`
alone does not prove the record is untouched. Two verified reasons, both from
sources in this repo:

  * `Writer.create` calls `create` and THEN a `search_count` to verify it
    (write_patterns.py:136-146); `Collab.todo` creates and THEN re-reads
    (collaboration.py:135-138). An `OdooError` from that verification step
    comes AFTER the record exists.
  * `Odoo._call_json2` turns EVERY `HTTPError` into `OdooError`
    (odoo_client.py:338-340), so a 502 from a proxy after Odoo committed
    arrives as the same class as a validation refusal.

So "nothing changed" is a claim only a `before_mutation` caller may make.
"""
import json
import sys
from pathlib import Path
from typing import Callable, Literal, NamedTuple

from mcp.server.mcpserver.exceptions import ToolError

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

# A Literal, not a bool: `phase="after_mutation_possible"` names the claim it
# licenses at the call site, where a bare `True` would name nothing.
Phase = Literal["before_mutation", "after_mutation_possible"]


class ToolExecutionError(ToolError):
    """Raised by `ToolOutcome.deliver()` so the SDK returns isError:true.

    Still deliberately NOT an `MCPError`: that one becomes a JSON-RPC protocol
    error with a numeric code, which the model never sees as tool output.
    `ToolError` is the SDK's APPLICATION-level failure and is not an
    `MCPError` — its bases are `MCPServerError`, `Exception`.

    It has to be this class rather than a plain exception, and the reason is
    version-dependent. SDK 2.0.0 forwarded any exception's text:

        except Exception as e:
            raise ToolError(f"Error executing tool {name}: {e}") from e

    SDK 2.1.0 split that in two, and only its own error type keeps the message:

        except (ToolError, ResourceError) as exc:
            raise ToolError(f"Error executing tool {name}: {exc}") from exc
        except Exception as exc:
            # A crash: the exception's own text stays on the server.
            raise UnexpectedToolError(f"Error executing tool {name}") from exc

    That is a deliberate decision not to leak arbitrary exception text to
    clients, and inheriting from `RuntimeError` put every one of our messages
    on the wrong side of it: on 2.1.0 a caller saw "Error executing tool
    search_read" instead of which credential was missing, which level a refusal
    needed, or why a guard fired. Measured against the built wheel, which
    resolves the newest 2.x while `uv.lock` holds development at 2.0.0 — so the
    unit suite stayed green while every published user lost the messages.
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
    phase: Phase = "after_mutation_possible",
) -> ToolOutcome:
    """Map an exception to what the tool should report. Never retries.

    `phase` is what the CALLER knows and this module cannot: whether a write
    had already been attempted when `exc` was raised. Only a caller that wrapped
    nothing but pre-flight work may pass `before_mutation`, which is the one
    phase allowed to say "nothing changed".

    It defaults to `after_mutation_possible` on purpose: a call site that forgets
    to say lands on caution, never on the false all-clear that invites the retry
    this whole module exists to prevent.

    `reread_state_fn` — typically `lambda: writer.state_of(model, ids)` — is
    called at most once, for every outcome whose state is in doubt.
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
        case OdooError() if phase == "before_mutation":
            return ToolOutcome(
                True,
                f"{exc}\nOdoo refused the call, so nothing changed. Fix the "
                f"arguments named above — repeating the same call cannot succeed.",
            )
        case _:
            return ToolOutcome(True, _uncertain(exc, reread_state_fn))


def _reread(reread_state_fn: Callable[[], object] | None) -> str:
    """The state as it was actually observed — never one that was not read."""
    if reread_state_fn is None:
        return "NOT RE-READ — no read was performed, so the state is unknown."
    try:
        return repr(reread_state_fn())
    except Exception as read_exc:  # a failed read proves nothing
        return f"RE-READ FAILED ({read_exc}) — the state is unknown."


def _uncertain(
    exc: BaseException,
    reread_state_fn: Callable[[], object] | None,
) -> str:
    """Text for a failure that may or may not have left a change behind."""
    return (
        f"UNCERTAIN: {exc}\n"
        f"The call may already have been applied before this failure.\n"
        f"Verified state: {_reread(reread_state_fn)}\n"
        f"Do NOT repeat the call blindly: decide from the state above, and "
        f"re-read the record when it says the state is unknown."
    )


def _committed(
    exc: OdooExecutedButUnserializable,
    reread_state_fn: Callable[[], object] | None,
) -> str:
    """Text for a write that landed but could not report itself."""
    return (
        f"COMMITTED but result unserializable. {exc}\n"
        f"Verified state: {_reread(reread_state_fn)}\n"
        f"Do NOT retry — the change is already applied; a second call would "
        f"duplicate it."
    )
