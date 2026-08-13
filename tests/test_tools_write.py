"""The four write tools: what they refuse, what they write, what they report.

Two things are being proven here, and they are different:

  * the GATE runs first and the Writer is never reached on a refusal — asserted
    as `writer.calls == []`, not as "the text says no";
  * what the tool REPORTS comes from the Writer's own before/after verdict, so
    "NO CHANGE" survives all the way out instead of being dressed up as done.

The ceiling is deleted from the environment by an autouse fixture, so every
test starts from `ODOO_MCP_MAX_LEVEL`'s default (3) and any test that needs
another value says so out loud.
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

SIX_IDS = [1, 2, 3, 4, 5, 6]
FIVE_IDS = [1, 2, 3, 4, 5]


@pytest.fixture(autouse=True)
def default_ceiling(monkeypatch):
    """Given: no host override — the ceiling is whatever the module defaults to."""
    monkeypatch.delenv("ODOO_MCP_MAX_LEVEL", raising=False)


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


def test_archiving_through_write_record_is_refused_at_the_default_ceiling(writer):
    """Given active=False, When written, Then it is judged destructive, not L1.

    Archiving hides the record — the same outcome as deleting it — so the
    classifier calls it L4 even though the method is `write`.
    """
    with pytest.raises(ToolExecutionError) as refusal:
        tools_write.write_record("res.partner", 7, {"active": False})

    assert "L4_DESTRUCTIVE" in str(refusal.value)
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
    read as a harmless L1.
    """
    seen = []

    def spy(*args):
        seen.append(args)
        return gate(*args)

    monkeypatch.setattr(tools_write, "gate", spy)

    run()

    assert seen == [expected]


def test_five_targets_stay_within_an_L1_ceiling(writer, monkeypatch):
    """Given a ceiling of 1, When 5 records are written, Then it is still L1."""
    monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", "1")

    tools_write.run_action("res.partner", "write", FIVE_IDS)

    assert writer.last_call["call"] == "act"
    assert writer.last_call["ids"] == FIVE_IDS


def test_six_targets_are_refused_as_a_batch(writer, monkeypatch):
    """Given a ceiling of 1, When 6 records are written, Then L2_BATCH refuses it.

    The pair with the test above is the point: one more id crosses the
    threshold, which only happens if the ids reached the classifier as a list.
    """
    monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", "1")

    with pytest.raises(ToolExecutionError) as refusal:
        tools_write.run_action("res.partner", "write", SIX_IDS)

    assert "L2_BATCH" in str(refusal.value)
    assert writer.calls == []


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
    """Given the default ceiling, When unlink is asked for, Then nothing is sent."""
    with pytest.raises(ToolExecutionError) as refusal:
        tools_write.run_action("res.partner", "unlink", [7])

    assert "L4_DESTRUCTIVE" in str(refusal.value)
    assert "ODOO_MCP_MAX_LEVEL" in str(refusal.value)
    assert writer.calls == []


def test_a_private_method_is_refused_even_with_the_ceiling_raised(writer,
                                                                  monkeypatch):
    """Given any ceiling, When a `_` method is asked for, Then it is still refused.

    Odoo rejects every private method itself, so no ceiling can make this one
    work — raising the bar must not look like a way around it.
    """
    monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", "99")

    with pytest.raises(ToolExecutionError) as refusal:
        tools_write.run_action("account.move", "_create_invoices", [7])

    assert "private" in str(refusal.value)
    assert writer.calls == []


# --------------------------------------------------------------- cancel_record
def test_cancel_record_is_refused_at_the_default_ceiling(writer):
    """Given the default ceiling, When a cancel is asked for, Then it is refused."""
    with pytest.raises(ToolExecutionError) as refusal:
        tools_write.cancel_record("sale.order", 7)

    assert "L4_DESTRUCTIVE" in str(refusal.value)
    assert "ODOO_MCP_MAX_LEVEL" in str(refusal.value)
    assert writer.calls == []


def test_cancel_record_repeats_the_gates_refusal_verbatim(writer):
    """Given a refusal, When it is surfaced, Then it is the gate's text, not a copy.

    A second explanation written here would drift from the one the gate keeps
    tested — and would start naming a ceiling it does not read.
    """
    with pytest.raises(ToolExecutionError) as refusal:
        tools_write.cancel_record("sale.order", 7)

    assert str(refusal.value) == gate("sale.order", "action_cancel", [7]).reason


def test_cancel_record_runs_action_cancel_once_the_ceiling_allows_it(writer,
                                                                     monkeypatch):
    """Given a ceiling of 4, When cancelling, Then action_cancel runs on that id."""
    monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", "4")
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

    monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", "4")
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
        read_only_hint=False, destructive_hint=False, idempotent_hint=False,
        open_world_hint=True)
    assert hints["write_record"] == ToolAnnotations(
        read_only_hint=False, destructive_hint=True, idempotent_hint=True,
        open_world_hint=True)
    assert hints["run_action"] == hints["cancel_record"] == ToolAnnotations(
        read_only_hint=False, destructive_hint=True, idempotent_hint=False,
        open_world_hint=True)
