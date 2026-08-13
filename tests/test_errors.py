"""Exception -> tool-result mapping.

The whole point of this module is that Odoo has THREE outcomes, not two:
succeeded, refused, and **executed-but-unreportable**. The third one commits
the write and then raises; treating it as a failure is what produced duplicate
invoices in the cold-start runs (references/SKILL.md rule 5). So the mapper
must be able to return a NON-error result for an exception, and it must never
state a record's state it did not actually re-read.

Real exception hierarchy in `odoo_scripts/odoo_client.py` (verified by reading
it, lines 108-133) — all four are direct `RuntimeError` subclasses, there is no
common `OdooError` base and no `AccessDenied` / `ValidationError` class:

    RuntimeError
      +- OdooError                        Odoo refused the call (also raised
      |                                   locally for `_`-prefixed methods)
      +- OdooExecutedButUnserializable    committed, return value unmarshalable
      +- MissingCredentials               connect-time, nothing was sent
      +- ProductionWriteBlocked           connect-time, nothing was sent

Access-denied and validation failures arrive as `OdooError` text, not as their
own classes.
"""
import inspect

import pytest
from odoo_client import (
    MissingCredentials,
    OdooError,
    OdooExecutedButUnserializable,
    ProductionWriteBlocked,
)

from odoo_assistant import server_errors
from odoo_assistant.server_errors import (
    ToolExecutionError,
    ToolOutcome,
    handle_odoo_exception,
)

# The message the real client raises (odoo_client.py:305-310), verbatim shape.
UNSERIALIZABLE = (
    "account.payment.action_post EXECUTED, but Odoo could not serialise "
    "its return value.\n"
    "The change IS applied. Do NOT retry — re-read the record and report "
    "its actual state."
)


def test_unserializable_commit_is_a_success_not_an_error():
    # Given a call that committed but could not serialise its return value
    exc = OdooExecutedButUnserializable(UNSERIALIZABLE)
    # When mapped
    outcome = handle_odoo_exception(exc)
    # Then it is NOT reported as a failed tool call, and it forbids a retry
    assert outcome.is_error is False
    assert "COMMITTED" in outcome.text
    assert "Do NOT retry" in outcome.text


def test_unserializable_commit_reports_the_state_it_actually_re_read():
    # Given a re-read callable standing in for Writer.state_of()
    calls = []

    def reread():
        calls.append(1)
        return {"id": 7, "state": "sale", "name": "SO0007"}

    # When the committed-but-unserializable case is mapped with it
    outcome = handle_odoo_exception(
        OdooExecutedButUnserializable(UNSERIALIZABLE), reread_state_fn=reread
    )
    # Then the re-read ran exactly once (never twice: a re-read is not a retry)
    assert len(calls) == 1
    # ...and its actual result is what the text reports
    assert outcome.is_error is False
    assert "'state': 'sale'" in outcome.text or '"state": "sale"' in outcome.text
    assert "SO0007" in outcome.text


def test_unserializable_commit_without_a_re_read_claims_no_state():
    # Given no way to re-read the record
    outcome = handle_odoo_exception(OdooExecutedButUnserializable(UNSERIALIZABLE))
    # Then the text says so instead of guessing (SKILL.md rule 5: an invoice
    # was once called "presumably draft" while it was posted)
    assert "NOT RE-READ" in outcome.text
    assert "presum" not in outcome.text.lower()


def test_failed_re_read_reports_the_failure_instead_of_a_guess():
    # Given a re-read that itself fails
    def reread():
        raise OdooError("account.move.search_read: serialisation error")

    # When mapped
    outcome = handle_odoo_exception(
        OdooExecutedButUnserializable(UNSERIALIZABLE), reread_state_fn=reread
    )
    # Then the write is still reported as committed (it is!), but the state is
    # reported as unknown, naming the read failure
    assert outcome.is_error is False
    assert "COMMITTED" in outcome.text
    assert "RE-READ FAILED" in outcome.text
    assert "serialisation error" in outcome.text
    assert "Do NOT retry" in outcome.text


def test_odoo_error_is_an_error_carrying_the_server_message():
    # Given Odoo refusing a write
    exc = OdooError("crm.lead.write: ValidationError: lost_reason is required")
    # When mapped
    outcome = handle_odoo_exception(exc)
    # Then the tool call is marked failed and the server message survives whole
    assert outcome.is_error is True
    assert "lost_reason is required" in outcome.text


def test_private_method_rejection_surfaces_intact():
    # Given the client's own local refusal of a `_`-prefixed method
    # (odoo_client.py:281 raises OdooError before any wire call)
    exc = OdooError(
        "sale.order._compute_amount: private methods are always rejected by "
        "Odoo (check_method_name). Use the public wizard instead."
    )
    # When mapped
    outcome = handle_odoo_exception(exc)
    # Then the actionable half of the message ("use the public wizard") is kept
    assert outcome.is_error is True
    assert "private methods" in outcome.text
    assert "public wizard" in outcome.text


def test_unexpected_exception_is_uncertain_not_failed():
    # Given a transport-level failure: the request may or may not have landed
    outcome = handle_odoo_exception(TimeoutError("connection reset by peer"))
    # Then it is an error, but the text refuses to claim the write did not happen
    assert outcome.is_error is True
    assert "UNCERTAIN" in outcome.text
    assert "connection reset by peer" in outcome.text
    assert "re-read" in outcome.text.lower()


@pytest.mark.parametrize(
    "exc",
    [
        MissingCredentials("XML-RPC transport needs ODOO_DB."),
        ProductionWriteBlocked("Refusing to write to app.persevida.com."),
    ],
    ids=["missing-credentials", "production-write-blocked"],
)
def test_connect_time_refusals_state_that_nothing_was_sent(exc):
    # Given a failure raised by connect() BEFORE any call reaches Odoo
    # (odoo_client.py:213/242/499/518)
    outcome = handle_odoo_exception(exc)
    # Then it is an error, and — unlike a timeout — the state is NOT uncertain
    assert outcome.is_error is True
    assert str(exc) in outcome.text
    assert "nothing was sent to Odoo" in outcome.text
    assert "UNCERTAIN" not in outcome.text


def test_unserializable_is_not_a_subclass_of_odoo_error():
    # The trap this module exists to defuse: the committed case is a SIBLING of
    # OdooError, not a subclass — `except OdooError` silently misses it, and a
    # caller who then retries posts the payment twice.
    assert not issubclass(OdooExecutedButUnserializable, OdooError)
    assert issubclass(OdooExecutedButUnserializable, RuntimeError)
    assert issubclass(OdooError, RuntimeError)
    # So the mapper must route on the concrete class, whatever the check order
    assert handle_odoo_exception(OdooExecutedButUnserializable("x")).is_error is False
    assert handle_odoo_exception(OdooError("x")).is_error is True


def test_deliver_encodes_is_error_the_way_the_sdk_reads_it():
    # SDK 2.0 (mcp/server/mcpserver/server.py:415-424) turns ANY exception a
    # tool raises into CallToolResult(content=[TextContent(str(e))],
    # is_error=True), and returns is_error=False for a normal return. So:
    assert ToolOutcome(is_error=False, text="ok").deliver() == "ok"
    with pytest.raises(ToolExecutionError) as caught:
        ToolOutcome(is_error=True, text="Odoo refused: bad field").deliver()
    # str(exc) is literally what the client sees — nothing may be lost in it
    assert str(caught.value) == "Odoo refused: bad field"


def test_no_custom_jsonrpc_error_codes_exist():
    # Custom codes are forbidden: isError inside the tool result is the whole
    # mechanism. MCPError(code, message) is the only path to a JSON-RPC code,
    # so our exception must not be one and must carry no code.
    assert not hasattr(ToolExecutionError("x"), "error")
    assert not hasattr(ToolExecutionError("x"), "code")
    # ...and the module cannot reach MCPError at all: it imports no SDK symbol
    # (the docstring names MCPError to explain the ban, so assert on imports,
    # never on prose).
    source = inspect.getsource(server_errors)
    assert "-32" not in source
    assert "import mcp" not in source
    assert "from mcp" not in source
    assert "MCPError" not in dir(server_errors)
