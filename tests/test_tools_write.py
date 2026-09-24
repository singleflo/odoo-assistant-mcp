"""The four write tools: what they refuse, what they write, what they report.

Two things are being proven here, and they are different:

  * the GATE runs first and the Writer is never reached on a refusal — asserted
    as `writer.calls == []`, not as "the text says no";
  * what the tool REPORTS comes from the Writer's own before/after verdict, so
    "NO CHANGE" survives all the way out instead of being dressed up as done.

The three gate variables are deleted from the environment by an autouse
fixture, so every test starts from the defaults — allow `*`, the default deny
list, unlink locked away — and any test that needs another value says so out
loud.
"""
import pytest
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from odoo_assistant import tools_write
from odoo_assistant.server_errors import ToolExecutionError
from odoo_assistant.server_safety import gate

from odoo_client import OdooError, OdooExecutedButUnserializable
from write_patterns import Writer
from tests.conftest import MockOdoo

FIVE_IDS = [1, 2, 3, 4, 5]


@pytest.fixture(autouse=True)
def default_lists(monkeypatch):
    """Given: no host override — all three gate variables are unset."""
    monkeypatch.delenv("ODOO_MCP_ALLOW", raising=False)
    monkeypatch.delenv("ODOO_MCP_DENY", raising=False)
    monkeypatch.delenv("ODOO_MCP_ALLOW_UNLINK", raising=False)


@pytest.fixture
def writer(mock_writer, monkeypatch):
    """Given: every tool resolves its Writer to the injected double."""
    monkeypatch.setattr(tools_write, "_writer", lambda: mock_writer)
    return mock_writer


def _writer_calls(writer, name):
    return [call for call in writer.calls if call["call"] == name]


class CommittedWriteOdoo(MockOdoo):
    def __init__(self, method, before, after):
        super().__init__()
        self.target_method = method
        self.before = before
        self.after = after
        self.committed = False

    def call(self, model, method, args=None, kwargs=None):
        if method == "read":
            value = self.after if self.committed else self.before
            self.set_results(model, [{"state": value, "note": value}], method="read")
        if method == self.target_method:
            self.committed = True
        return super().call(model, method, args, kwargs)


# --------------------------------------------------------------- create_record
def test_create_reports_the_id_it_minted(writer):
    """Given a plain create, When it runs, Then the new id is in the answer."""
    text = tools_write.create_record("res.partner", {"name": "ACME"})

    assert text == "Created (or reused) res.partner id=1"


def test_create_hands_the_values_to_the_writers_own_parameter(writer):
    """Given values, When created, Then they arrive as `vals` — the real name."""
    tools_write.create_record("res.partner", {"name": "ACME"}, unique_on=["name"])

    assert writer.last_call["call"] == "create"
    assert writer.last_call["vals"] == {"name": "ACME"}
    assert writer.last_call["unique_on"] == ["name"]


def test_the_same_unique_on_twice_reuses_one_record(writer):
    """Given a matching `unique_on`, When create runs twice, Then one record exists.

    The proof is `created_ids`: the writer is asked twice on purpose (that is
    what a retrying agent does) and mints exactly one id, returning it both
    times. Counting `create` calls would prove nothing — the guard lives
    *inside* the writer, so the second call is exactly what it has to survive.
    """
    first = tools_write.create_record("res.partner", {"name": "ACME"},
                                      unique_on=["name"])
    second = tools_write.create_record("res.partner", {"name": "ACME"},
                                       unique_on=["name"])

    assert first == second
    assert writer.created_ids["res.partner"] == [1]
    assert writer.log[-1].duplicate_avoided is True


# ---------------------------------------------------------------- write_record
def test_write_reports_the_before_and_after_values(writer):
    """Given a record holding OLD, When NEW is written, Then both are reported."""
    writer.set_record("sale.order", 7, {"client_order_ref": "OLD"})

    text = tools_write.write_record("sale.order", 7, {"client_order_ref": "NEW"})

    assert "before: 'OLD' -> after: 'NEW'" in text


def test_write_reports_not_changed_when_the_value_is_already_there(writer):
    """Given the value is already set, When written again, Then NOT CHANGED.

    The call succeeds either way; only the comparison tells them apart, and
    reporting this one as done is how a run claims work it never did.
    """
    writer.set_record("sale.order", 7, {"client_order_ref": "SAME"})

    text = tools_write.write_record("sale.order", 7, {"client_order_ref": "SAME"})

    assert text.startswith("NOT CHANGED")
    assert "'SAME'" in text


def test_archiving_through_write_record_is_refused_by_the_default_deny_list(writer):
    """Given active=False, When written, Then it is refused as `archive`.

    Archiving hides the record — the same outcome as deleting it — so the
    write carries the virtual entry `archive` into the lists even though the
    method is `write`.
    """
    with pytest.raises(ToolExecutionError) as refusal:
        tools_write.write_record("res.partner", 7, {"active": False})

    assert "refused by ODOO_MCP_DENY" in str(refusal.value)
    assert "'archive'" in str(refusal.value)
    assert writer.calls == []


# ------------------------------------------------------------ the gate's input
@pytest.mark.parametrize(("run", "expected"), [
    (lambda: tools_write.create_record("res.partner", {"name": "ACME"}),
     ("res.partner", "create", None, {"name": "ACME"})),
    (lambda: tools_write.write_record("sale.order", 7, {"note": "x"}),
     ("sale.order", "write", 7, {"note": "x"})),
    (lambda: tools_write.run_action("sale.order", "action_confirm", [7]),
     ("sale.order", "action_confirm", [7], None)),
])
def test_the_gate_is_fed_the_execute_kw_positional_shape(writer, monkeypatch,
                                                         run, expected):
    """Given a tool call, When it gates, Then ids and values land in their slots.

    A spy that still delegates to the real gate: the shape is what matters —
    passing a dict where `execute_kw` wants a list makes a 600-record archive
    read as a harmless write.
    """
    seen = []

    def spy(*args):
        seen.append(args)
        return gate(*args)

    monkeypatch.setattr(tools_write, "gate", spy)

    run()

    assert seen == [expected]


def test_five_targets_run_when_the_method_is_allowed(writer, monkeypatch):
    """Given `create,write` on ODOO_MCP_ALLOW, When 5 records are written, Then all 5 go.

    The gate judges the method name, not how many ids carry it — there is no
    batch threshold to stay under — but the ids must still reach it in
    `execute_kw`'s list shape.
    """
    monkeypatch.setenv("ODOO_MCP_ALLOW", "create,write")

    tools_write.run_action("res.partner", "write", FIVE_IDS)

    assert writer.last_call["call"] == "act"
    assert writer.last_call["ids"] == FIVE_IDS


# ------------------------------------------------------------------ run_action
def test_run_action_confirms_and_reports_the_state_change(writer):
    """Given a draft order, When confirmed, Then the state transition is reported."""
    writer.set_record("sale.order", 7, {"state": "draft"})
    writer.set_effect("sale.order", "action_confirm", {"state": "sale"})

    text = tools_write.run_action("sale.order", "action_confirm", [7])

    assert "'draft' -> 'sale'" in text
    assert "CHANGED" in text
    assert writer.last_call["method"] == "action_confirm"
    assert writer.last_call["watch"] == "state"


def test_an_action_that_changed_nothing_says_so(writer):
    """Given no effect, When the action runs, Then the report is NO CHANGE."""
    writer.set_record("sale.order", 7, {"state": "draft"})

    text = tools_write.run_action("sale.order", "action_confirm", [7])

    assert "NO CHANGE" in text


def test_unlink_is_refused_and_no_write_is_attempted(writer):
    """Given the default lists, When unlink is asked for, Then nothing is sent.

    Deletion is decided before the lists are read: only
    ODOO_MCP_ALLOW_UNLINK=yes can grant it, so the refusal names that variable.
    """
    with pytest.raises(ToolExecutionError) as refusal:
        tools_write.run_action("res.partner", "unlink", [7])

    assert "ODOO_MCP_ALLOW_UNLINK" in str(refusal.value)
    assert writer.calls == []


def test_a_padded_allow_entry_matches_once_trimmed(writer, monkeypatch):
    """Given `" write , create "`, When write is gated, Then trimming admits it."""
    monkeypatch.setenv("ODOO_MCP_ALLOW", " write , create ")
    writer.set_record("sale.order", 7, {"note": "OLD"})

    tools_write.write_record("sale.order", 7, {"note": "NEW"})

    assert writer.last_call["call"] == "write"
    assert writer.last_call["vals"] == {"note": "NEW"}


def test_a_method_outside_that_same_allow_list_is_refused_naming_it(writer,
                                                                    monkeypatch):
    """Given the same padded list, When action_confirm is gated, Then refused.

    The pair with the test above is the point: one variable, one method
    admitted and one refused, so the match up there came from entries read as
    `write`/`create` rather than from a list that lets everything through.
    """
    monkeypatch.setenv("ODOO_MCP_ALLOW", " write , create ")

    with pytest.raises(ToolExecutionError) as refusal:
        tools_write.run_action("sale.order", "action_confirm", [7])

    assert "not in ODOO_MCP_ALLOW" in str(refusal.value)
    assert writer.calls == []


# --------------------------------------------------------------- cancel_record
def test_cancel_record_is_refused_by_the_default_deny_list(writer):
    """Given the default deny list, When a cancel is asked for, Then it is refused."""
    with pytest.raises(ToolExecutionError) as refusal:
        tools_write.cancel_record("sale.order", 7)

    assert "refused by ODOO_MCP_DENY" in str(refusal.value)
    assert "'action_cancel'" in str(refusal.value)
    assert writer.calls == []


def test_cancel_record_repeats_the_gates_refusal_verbatim(writer):
    """Given a refusal, When it is surfaced, Then it is the gate's text, not a copy.

    A second explanation written here would drift from the one the gate keeps
    tested — and would start naming a variable it does not read.
    """
    with pytest.raises(ToolExecutionError) as refusal:
        tools_write.cancel_record("sale.order", 7)

    assert str(refusal.value) == gate("sale.order", "action_cancel", [7]).reason


def test_cancel_record_runs_action_cancel_once_the_deny_list_admits_it(writer,
                                                                       monkeypatch):
    """Given ODOO_MCP_DENY=unlink, When cancelling, Then action_cancel runs on that id.

    A set value REPLACES the default list entirely, so dropping `action_cancel`
    from it is all the enablement there is.
    """
    monkeypatch.setenv("ODOO_MCP_DENY", "unlink")
    writer.set_record("sale.order", 7, {"state": "sale"})
    writer.set_effect("sale.order", "action_cancel", {"state": "cancel"})

    text = tools_write.cancel_record("sale.order", 7)

    assert writer.last_call["method"] == "action_cancel"
    assert writer.last_call["ids"] == [7]
    assert "'sale' -> 'cancel'" in text


# ------------------------------------------------- committed but unserializable
@pytest.mark.parametrize(("tool", "method"), [
    pytest.param(
        lambda: tools_write.create_record("res.partner", {"name": "ACME"}),
        "create_record",
    ),
    pytest.param(
        lambda: tools_write.write_record("sale.order", 7, {"note": "NEW"}),
        "write_record",
    ),
    pytest.param(
        lambda: tools_write.run_action("sale.order", "action_confirm", [7]),
        "run_action",
    ),
    pytest.param(
        lambda: tools_write.cancel_record("sale.order", 7),
        "cancel_record",
    ),
])
def test_missing_credentials_are_mapped_for_every_write_tool(
        monkeypatch, tool, method):
    from odoo_client import MissingCredentials

    # The credentials are what is under test, so every tool has to reach the
    # Writer: a DENY holding only `unlink` replaces the default list and lets
    # `cancel_record`'s `action_cancel` past the gate.
    monkeypatch.setenv("ODOO_MCP_DENY", "unlink")
    monkeypatch.setattr(
        tools_write, "_writer",
        lambda: (_ for _ in ()).throw(MissingCredentials("missing credentials")),
    )

    with pytest.raises(ToolExecutionError) as failure:
        tool()

    assert "nothing was sent to Odoo" in str(failure.value), method


def test_real_writer_surfaces_a_swallowed_write_serialisation_failure(monkeypatch):
    client = CommittedWriteOdoo("write", "OLD", "NEW")
    client.set_results(
        "sale.order", OdooExecutedButUnserializable("cannot marshal None"),
        method="write",
    )
    monkeypatch.setattr(tools_write, "_writer", lambda: Writer(client))

    text = tools_write.write_record("sale.order", 7, {"note": "NEW"})

    assert "COMMITTED but result unserializable" in text
    assert "Post-write observation: 'NEW'" in text
    assert "Do NOT retry" in text


def test_real_writer_surfaces_a_swallowed_action_serialisation_failure(monkeypatch):
    client = CommittedWriteOdoo("action_post", "draft", "posted")
    client.set_results(
        "account.payment", OdooExecutedButUnserializable("cannot marshal None"),
        method="action_post",
    )
    monkeypatch.setattr(tools_write, "_writer", lambda: Writer(client))

    text = tools_write.run_action("account.payment", "action_post", [7])

    assert "COMMITTED but result unserializable" in text
    assert "Post-write observation: 'posted'" in text
    assert "Do NOT retry" in text


@pytest.fixture
def committed_action(writer, monkeypatch):
    """Given: the action lands in Odoo and then fails to serialise its reply."""
    def act_that_committed(*_args, **_kwargs):
        raise OdooExecutedButUnserializable("cannot marshal None")

    monkeypatch.setattr(writer, "act", act_that_committed)
    writer.set_record("account.payment", 7, {"state": "posted"})
    return writer


def test_a_committed_action_is_reported_as_success_not_as_a_failure(committed_action):
    """When the reply cannot be serialised, Then the tool returns text, not an error.

    Odoo commits before serialising, so this exception means the payment IS
    posted. Reporting a failure invites the retry that posts it twice.
    """
    text = tools_write.run_action("account.payment", "action_post", [7])

    assert "COMMITTED" in text
    assert "Do NOT retry" in text


def test_a_committed_action_re_reads_the_state_exactly_once(committed_action):
    """When the reply cannot be serialised, Then the state is read, never assumed."""
    text = tools_write.run_action("account.payment", "action_post", [7])

    assert len(_writer_calls(committed_action, "state_of")) == 1
    assert _writer_calls(committed_action, "state_of")[0]["ids"] == [7]
    assert "'state': 'posted'" in text


@pytest.fixture
def refused_action(writer, monkeypatch):
    """Given: the action raised OdooError, with the order already confirmed.

    An OdooError around a write proves nothing about the record: `Writer.create`
    raises it from the `search_count` that VERIFIES the create
    (write_patterns.py:141-146), and `_call_json2` raises it for any HTTPError
    (odoo_client.py:338) — a 502 from a proxy after Odoo committed included.
    """
    def act_that_was_refused(*_args, **_kwargs):
        raise OdooError("sale.order(7,) is not in a state requiring confirmation")

    monkeypatch.setattr(writer, "act", act_that_was_refused)
    writer.set_record("sale.order", 7, {"state": "sale"})
    return writer


def test_an_odoo_refusal_is_delivered_as_an_error(refused_action):
    """Given Odoo refuses, When the action runs, Then the tool raises with its text."""
    with pytest.raises(ToolExecutionError) as failure:
        tools_write.run_action("sale.order", "action_confirm", [7])

    assert "not in a state requiring confirmation" in str(failure.value)


def test_an_odoo_refusal_after_a_write_never_claims_nothing_changed(refused_action):
    """Given the same refusal, Then it does NOT report the record as untouched.

    The claim "nothing changed" is what invites the retry, and a retry is what
    produced four identical customers and two orphan invoices in the cold-start
    runs. Only a caller that wrapped pre-flight work alone may make it.
    """
    with pytest.raises(ToolExecutionError) as failure:
        tools_write.run_action("sale.order", "action_confirm", [7])

    assert "nothing changed" not in str(failure.value)
    assert "repeating the same call cannot succeed" not in str(failure.value)


def test_an_odoo_refusal_after_a_write_reports_the_state_it_re_read(refused_action):
    """Given the same refusal, Then the answer is the state actually read back.

    'state: sale' is the whole answer to "did my confirm land?" — and it comes
    from a read, never from the exception text.
    """
    with pytest.raises(ToolExecutionError) as failure:
        tools_write.run_action("sale.order", "action_confirm", [7])

    assert "'state': 'sale'" in str(failure.value)
    assert len(_writer_calls(refused_action, "state_of")) == 1


# ------------------------------------------------------------------- the wiring
def test_the_default_writer_wraps_the_shared_client(mock_odoo):
    """Given no injection, When a tool needs a Writer, Then it wraps the client."""
    built = tools_write._writer()

    assert isinstance(built, Writer)
    assert built.o is mock_odoo


def test_register_publishes_the_four_tools_with_truthful_annotations():
    """Given a server, When register runs, Then the four tools carry honest hints.

    `list_tools()` on the server is async; the tool manager's is not, which is
    the whole reason this test reaches for it.
    """
    mcp = MCPServer("test")

    tools_write.register(mcp)

    hints = {tool.name: tool.annotations for tool in mcp._tool_manager.list_tools()}
    assert set(hints) == {"create_record", "write_record", "run_action",
                          "cancel_record"}
    assert hints["create_record"] == ToolAnnotations(
        title="Create a record",
        read_only_hint=False, destructive_hint=False, idempotent_hint=False,
        open_world_hint=True)
    assert hints["write_record"] == ToolAnnotations(
        title="Update a record",
        read_only_hint=False, destructive_hint=True, idempotent_hint=True,
        open_world_hint=True)
    assert hints["run_action"] == ToolAnnotations(
        title="Run a workflow action",
        read_only_hint=False, destructive_hint=True, idempotent_hint=False,
        open_world_hint=True)
    assert hints["cancel_record"] == ToolAnnotations(
        title="Cancel a record",
        read_only_hint=False, destructive_hint=True, idempotent_hint=False,
        open_world_hint=True)
