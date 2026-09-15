"""The gate between a tool call and Odoo: two lists, read fresh on every call.

These tests call the REAL `gate()` — it is pure logic with no Odoo I/O, so no
double is needed and none is used. The only input is the environment, which is
exactly the surface an operator configures, so every test states its variables
in the Given and the refusal text it expects in the Then.

Two properties are worth more than the individual cases:

  * Matching is EXACT. A prefix or substring match would let
    `action_send_and_print` ride in on an `action_send` entry the operator
    never wrote, so the paired tests — `mailing.mailing` denied, the evolution
    wizard allowed — pin both directions of the same exactness.
  * The argument shape is checked by its OBSERVABLE effect (`active: False`
    through the `values` PARAMETER refuses the call). The archive detector
    reads `args` positionally, so a dict smuggled past `_positional_args`
    would quietly re-allow archiving as a harmless write — the bug
    `collaboration.py::_guard` has, pinned here so this wrapper never
    inherits it.
"""
import pytest

from odoo_assistant import server_safety
from odoo_assistant.server_safety import (
    DEFAULT_DENY,
    GateResult,
    allowed_methods,
    denied_methods,
    gate,
    refuse_legacy_environment,
    unlink_allowed,
)


@pytest.fixture(autouse=True)
def clean_lists(monkeypatch):
    """Given none of the gate's variables in the environment, unless a test sets one.

    All three helpers read the environment at call time, so a value leaked
    from the developer's shell would silently rewrite every expectation below.
    ODOO_MCP_MAX_LEVEL is deleted too: it is the removed ceiling variable and
    its presence now means a startup refusal, not a gate input.
    """
    for name in ("ODOO_MCP_ALLOW", "ODOO_MCP_DENY",
                 "ODOO_MCP_ALLOW_UNLINK", "ODOO_MCP_MAX_LEVEL"):
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------- the defaults
def test_write_is_allowed_by_default():
    """Given no variables at all, When a one-record write is gated,
    Then it is allowed — the wildcard default keeps the server useful."""
    decision = gate("res.partner", "write", [1], {"name": "ACME"})

    assert decision.allowed is True


def test_state_change_is_allowed_by_default():
    """Given no variables, When action_confirm is gated, Then it is allowed —
    confirming orders is the work the agent is here for."""
    decision = gate("sale.order", "action_confirm", [1])

    assert decision.allowed is True


def test_unlink_is_refused_by_default():
    """Given no variables, When unlink is gated, Then it is refused and the
    refusal names the one switch that can grant it."""
    decision = gate("res.partner", "unlink", [1])

    assert decision.allowed is False
    assert "ODOO_MCP_ALLOW_UNLINK=yes" in decision.reason


@pytest.mark.parametrize("method", [
    "action_cancel", "button_cancel", "action_reverse", "action_draft"])
def test_destructive_actions_are_denied_by_default(method):
    """Given the default deny list, When a destructive action is gated,
    Then it is refused and the refusal names ODOO_MCP_DENY and the entry."""
    decision = gate("sale.order", method, [1])

    assert decision.allowed is False
    assert "ODOO_MCP_DENY" in decision.reason
    assert method in decision.reason


def test_action_archive_is_denied_by_default():
    """Given the default deny list, When action_archive is gated, Then it is
    refused through its virtual `archive` name — archiving reads like deletion."""
    decision = gate("res.partner", "action_archive", [1])

    assert decision.allowed is False
    assert "ODOO_MCP_DENY" in decision.reason


def test_archiving_write_is_denied_by_default():
    """Given values that set active False, When the write is gated, Then it is
    refused as `archive` even though the method itself is `write` —
    classification by effect lives on as a virtual name."""
    decision = gate("res.partner", "write", [1], {"active": False})

    assert decision.allowed is False
    assert "ODOO_MCP_DENY" in decision.reason


def test_mass_mailing_send_is_denied_by_default_but_not_on_other_models():
    """Given the default deny list, When `action_send` is gated per model,
    Then `mailing.mailing` is refused by its model-qualified entry while the
    same method on evolution's test wizard passes — one deliberate default
    denial, exact by model, not a ban on the method name."""
    denied = gate("mailing.mailing", "action_send", [1])
    assert denied.allowed is False
    assert "mailing.mailing:action_send" in denied.reason
    assert "ODOO_MCP_DENY" in denied.reason

    assert gate("evolution.send.test.wizard", "action_send", [1]).allowed is True


def test_unknown_method_passes_under_the_wildcard():
    """Given no variables, When a method no whitelist ever knew is gated,
    Then it is allowed — `*` means every method not denied; refusing what
    nobody classified was 0.1.x's rule and it is gone by design."""
    decision = gate("evolution.chat", "send_message_from_ui", [1])

    assert decision.allowed is True


# --------------------------------------------------------- the lists, exactly
def test_a_model_qualified_deny_refuses_exactly_that_pair(monkeypatch):
    """Given DENY=evolution.chat:send_message_from_ui, When the bare method is
    gated on other models, Then only the named pair is refused — the model
    qualifier is part of the entry and matching is exact."""
    monkeypatch.setenv("ODOO_MCP_DENY", "evolution.chat:send_message_from_ui")

    refused = gate("evolution.chat", "send_message_from_ui", [1])
    assert refused.allowed is False
    assert "evolution.chat:send_message_from_ui" in refused.reason

    assert gate("res.partner", "send_message_from_ui", [1]).allowed is True


def test_deny_matching_is_exact_whole_entry(monkeypatch):
    """Given DENY=action_send, When action_send_and_print is gated, Then it is
    allowed — a prefix or substring match would refuse a method the operator
    never wrote, and the send-and-print flow must keep working by default."""
    monkeypatch.setenv("ODOO_MCP_DENY", "action_send")

    assert gate("sale.order", "action_send_and_print", [1]).allowed is True


def test_a_set_deny_list_replaces_the_default(monkeypatch):
    """Given DENY=unlink, When action_cancel is gated, Then it passes — the
    operator's list REPLACES the default, which is how an entry is re-enabled.
    Unlink itself stays refused: only the dedicated switch can grant it."""
    monkeypatch.setenv("ODOO_MCP_DENY", "unlink")

    assert gate("sale.order", "action_cancel", [1]).allowed is True

    refused = gate("res.partner", "unlink", [1])
    assert refused.allowed is False
    assert "ODOO_MCP_ALLOW_UNLINK" in refused.reason


def test_allow_none_makes_the_server_read_only(monkeypatch):
    """Given ALLOW=none, When create and search_read are gated, Then the write
    is refused naming the read-only setting and the read still passes."""
    monkeypatch.setenv("ODOO_MCP_ALLOW", "none")

    refused = gate("res.partner", "create", None, {"name": "ACME"})
    assert refused.allowed is False
    assert "read-only" in refused.reason
    assert "ODOO_MCP_ALLOW=none" in refused.reason

    assert gate("res.partner", "search_read", []).allowed is True


def test_an_allow_set_refuses_whatever_it_does_not_list(monkeypatch):
    """Given ALLOW=create,write, When action_confirm is gated, Then it is
    refused naming ODOO_MCP_ALLOW, while both listed methods pass."""
    monkeypatch.setenv("ODOO_MCP_ALLOW", "create,write")

    assert gate("res.partner", "create", None, {"name": "ACME"}).allowed is True
    assert gate("res.partner", "write", [1], {"name": "ACME"}).allowed is True

    refused = gate("sale.order", "action_confirm", [1])
    assert refused.allowed is False
    assert "ODOO_MCP_ALLOW" in refused.reason


def test_entries_with_spaces_around_commas_are_trimmed(monkeypatch):
    """Given lists written with slack — ' create , write ' and
    ' action_cancel , unlink ' — When the same calls are gated as with tight
    lists, Then the behaviour is identical: a human edits these in a config
    file, and spaces must neither widen nor narrow what they approved."""
    monkeypatch.setenv("ODOO_MCP_ALLOW", " create , write ")
    monkeypatch.setenv("ODOO_MCP_DENY", " action_cancel , unlink ")

    assert gate("res.partner", "create", None, {"name": "ACME"}).allowed is True
    assert gate("res.partner", "write", [1], {"note": "x"}).allowed is True
    assert gate("sale.order", "action_confirm", [1]).allowed is False

    cancelled = gate("sale.order", "action_cancel", [1])
    assert cancelled.allowed is False
    assert "'action_cancel'" in cancelled.reason


def test_reads_are_never_subject_to_the_lists(monkeypatch):
    """Given a deny entry naming a read method AND ALLOW=none, When that read
    is gated, Then it still passes — the lists govern effects and a read has
    none, so reads are decided before either list is consulted."""
    monkeypatch.setenv("ODOO_MCP_ALLOW", "none")
    monkeypatch.setenv("ODOO_MCP_DENY", "search_read")

    assert gate("sale.order", "search_read", []).allowed is True


# -------------------------------------------------------- the unlink hard stop
def test_allow_unlink_yes_grants_unlink(monkeypatch):
    """Given ODOO_MCP_ALLOW_UNLINK=yes, When unlink is gated, Then it passes —
    the dedicated switch is the only key."""
    monkeypatch.setenv("ODOO_MCP_ALLOW_UNLINK", "yes")

    assert gate("res.partner", "unlink", [1]).allowed is True


def test_unlink_switch_reads_only_its_explicit_spellings(monkeypatch):
    """Given each spelling an operator might type, When unlink_allowed() is
    read, Then exactly yes/true/1 (any case) is True and everything else is
    False — 'on' or 'y' must not half-count as consent to delete."""
    for raw, expected in [("yes", True), ("TRUE", True), ("1", True),
                          ("no", False), ("y", False), ("", False)]:
        monkeypatch.setenv("ODOO_MCP_ALLOW_UNLINK", raw)
        assert unlink_allowed() is expected


def test_an_allow_entry_cannot_grant_unlink(monkeypatch):
    """Given ALLOW=unlink — the entry an optimistic agent would try — When
    unlink is gated, Then it is still refused: only the dedicated variable
    decides, and the refusal says so."""
    monkeypatch.setenv("ODOO_MCP_ALLOW", "unlink")

    decision = gate("res.partner", "unlink", [1])

    assert decision.allowed is False
    assert "ODOO_MCP_ALLOW_UNLINK" in decision.reason


def test_unlink_is_decided_before_the_lists(monkeypatch):
    """Given ALLOW=none AND unlink not granted, When unlink is gated, Then the
    refusal names ODOO_MCP_ALLOW_UNLINK and not the read-only setting — the
    hardcoded guard answers before any list could."""
    monkeypatch.setenv("ODOO_MCP_ALLOW", "none")

    decision = gate("res.partner", "unlink", [1])

    assert decision.allowed is False
    assert "ODOO_MCP_ALLOW_UNLINK" in decision.reason
    assert "read-only" not in decision.reason


def test_deny_set_to_empty_still_means_the_default(monkeypatch):
    """Given ALLOW=* and DENY explicitly set to the empty string, When unlink
    is gated, Then it is still refused — empty means unset (the XDG
    convention), so an explicit blank cannot wipe the default deny list."""
    monkeypatch.setenv("ODOO_MCP_ALLOW", "*")
    monkeypatch.setenv("ODOO_MCP_DENY", "")

    decision = gate("res.partner", "unlink", [1])

    assert decision.allowed is False
    assert "ODOO_MCP_ALLOW_UNLINK" in decision.reason


# --------------------------------------------------------- what no list can buy
@pytest.mark.parametrize("allow", ["*", "_create_invoices,write"])
def test_private_methods_are_refused_whatever_the_lists_say(monkeypatch, allow):
    """Given any allow list — even one naming the private method — When a
    `_`-prefixed method is gated, Then it is refused: Odoo rejects private
    methods (check_method_name), so no configuration can buy them."""
    monkeypatch.setenv("ODOO_MCP_ALLOW", allow)

    decision = gate("account.move", "_create_invoices", [1])

    assert decision.allowed is False
    assert "check_method_name" in decision.reason
    assert "ODOO_MCP" not in decision.reason   # no variable would help


# ------------------------------------------------ the shape the gate receives
def test_values_parameter_with_active_false_is_detected():
    """Given `active: False` through the `values` parameter, with and without
    ids, When the write is gated, Then both shapes are refused —
    `_positional_args` must land the values dict where the archive detector
    reads it (`args`), and a dict-shaped regression would quietly re-allow it
    as a harmless write."""
    without_ids = gate("res.partner", "write", None, {"active": False})
    with_ids = gate("res.partner", "write", [1], {"active": False})

    assert without_ids.allowed is False
    assert with_ids.allowed is False
    assert "archive" in with_ids.reason


def test_a_bare_int_id_behaves_as_a_one_element_list():
    """Given a single int instead of a list, When gated, Then it behaves as
    [id] — `Writer.write`/`Writer.act` normalise the same way, and the gate
    must agree with what actually reaches Odoo."""
    assert gate("sale.order", "action_confirm", 1) == gate("sale.order", "action_confirm", [1])


# --------------------------------------------------------- the structural guard
def test_account_move_read_without_move_type_is_refused():
    """Given an account.move read with no move_type filter, When gated, Then
    the structural guard refuses it — reads bypass the lists but not
    check_guards, which keeps 3.613 mixed records from becoming one number."""
    decision = gate("account.move", "search_count", [["state", "=", "posted"]])

    assert decision.allowed is False
    assert "move_type" in decision.reason


def test_account_move_read_with_move_type_passes():
    """Given the same read WITH the filter, When gated, Then it is allowed."""
    decision = gate("account.move", "search_read", [["move_type", "=", "out_invoice"]])

    assert decision.allowed is True


# -------------------------------------------------------------- the surface
def test_gate_result_carries_only_allowed_and_reason():
    """Given the module, When the GateResult fields are listed, Then they are
    exactly `allowed` and `reason` — the old `level` field had no consumer,
    and a decision is a boolean plus a sentence an agent can relay."""
    assert GateResult._fields == ("allowed", "reason")


def test_module_exposes_only_the_new_gate_surface():
    """Given the module, When its public names are listed, Then the two-list
    API is there and every ceiling name is gone — importing `max_level` must
    fail loudly in server.py's migration, not keep a zombie alive."""
    public = {name for name in vars(server_safety) if not name.startswith("_")}

    assert {"DEFAULT_DENY", "GateResult", "gate", "allowed_methods",
            "denied_methods", "unlink_allowed",
            "refuse_legacy_environment"} <= public
    assert not hasattr(server_safety, "max_level")
    assert not hasattr(server_safety, "LEVEL_ORDINALS")
    assert not hasattr(server_safety, "DEFAULT_MAX_LEVEL")


# ----------------------------------------------------- parsing and startup
def test_allowed_methods_parses_wildcard_none_and_sets(monkeypatch):
    """Given each shape ODOO_MCP_ALLOW can take, When allowed_methods() is
    read, Then unset and empty both mean `*` (empty means unset, the XDG
    convention), the literal `none` is the read-only sentinel, and anything
    else is the entry set with case preserved."""
    assert allowed_methods() == "*"

    monkeypatch.setenv("ODOO_MCP_ALLOW", "")
    assert allowed_methods() == "*"

    monkeypatch.setenv("ODOO_MCP_ALLOW", "*")
    assert allowed_methods() == "*"

    monkeypatch.setenv("ODOO_MCP_ALLOW", "none")
    assert allowed_methods() == "none"

    monkeypatch.setenv("ODOO_MCP_ALLOW", "create,Write")
    assert allowed_methods() == {"create", "Write"}


def test_denied_methods_default_and_replacement(monkeypatch):
    """Given ODOO_MCP_DENY unset and then set, When denied_methods() is read,
    Then unset means DEFAULT_DENY and a value replaces it wholesale."""
    assert denied_methods() == set(DEFAULT_DENY)

    monkeypatch.setenv("ODOO_MCP_DENY", "unlink")
    assert denied_methods() == {"unlink"}


def test_refuse_legacy_environment_names_the_replacements(monkeypatch):
    """Given ODOO_MCP_MAX_LEVEL=3 — any value counts — When the legacy check
    runs, Then startup is refused and the message names all three replacement
    variables."""
    monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", "3")

    with pytest.raises(RuntimeError) as excinfo:
        refuse_legacy_environment()

    message = str(excinfo.value)
    assert "ODOO_MCP_ALLOW" in message
    assert "ODOO_MCP_DENY" in message
    assert "ODOO_MCP_ALLOW_UNLINK" in message


@pytest.mark.parametrize("raw", [None, ""])
def test_refuse_legacy_environment_ignores_unset_and_empty(monkeypatch, raw):
    """Given the variable absent (the fixture deleted it) or set to the empty
    string — empty means unset, `paths._from_env`'s convention — When the
    legacy check runs, Then it returns None and startup proceeds."""
    if raw is not None:
        monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", raw)

    assert refuse_legacy_environment() is None
