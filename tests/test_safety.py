"""The gate between a tool call and Odoo: every level is computed, never assumed.

These tests call the REAL `classify()` from `safety_layer.py` — it is pure
logic with no Odoo I/O, so no double is needed and none is used. Whatever the
gate reports here is what the shipped classifier really answers.

Two properties are worth more than the individual cases:

  * `LEVEL_ORDINALS` is checked against the level literals found IN the source
    of `classify()`, so a level added to `safety_layer.py` tomorrow fails this
    suite instead of silently landing in the "unknown, therefore blocked" bin.
  * The argument shape is checked by its OBSERVABLE effect (a 6-id write is
    L2_BATCH, an `active: False` write is L4_DESTRUCTIVE). Both detections are
    positional: pass a dict where `execute_kw` expects `[ids, vals]` and they
    quietly stop firing, which is the bug `collaboration.py::_guard` has and
    the one `test_dict_shaped_args_*` pins so this wrapper never inherits it.
"""
import inspect
import re

import pytest

from odoo_assistant import server_safety
from odoo_assistant.server_safety import DEFAULT_MAX_LEVEL, LEVEL_ORDINALS, gate, max_level

from safety_layer import BATCH_THRESHOLD, classify  # noqa: E402  (conftest bootstrap)


@pytest.fixture(autouse=True)
def default_ceiling(monkeypatch):
    """Given: no ODOO_MCP_MAX_LEVEL in the environment, unless a test sets one.

    `max_level()` reads the variable at call time, so a leaked value from the
    developer's shell would silently rewrite every expectation below.
    """
    monkeypatch.delenv("ODOO_MCP_MAX_LEVEL", raising=False)


# --------------------------------------------------------------- the ordinals
def test_ordinals_cover_every_level_classify_can_return():
    """Given the real classify(), When its source is scanned for level literals,
    Then LEVEL_ORDINALS knows exactly those and no others."""
    returned = set(re.findall(r'return "(L\d_[A-Z_]+)"', inspect.getsource(classify)))

    assert returned == set(LEVEL_ORDINALS)


def test_ordinals_are_ordered_by_severity():
    """Given the ordinal table, When read in order, Then it ranks L0 < L1 < L2 < L3 < L4,
    with both L5 variants sharing the top rank."""
    assert [LEVEL_ORDINALS[name] for name in
            ("L0_READ", "L1_WRITE", "L2_BATCH", "L3_STATE_CHANGE", "L4_DESTRUCTIVE")
            ] == [0, 1, 2, 3, 4]
    assert LEVEL_ORDINALS["L5_UNKNOWN"] == LEVEL_ORDINALS["L5_PRIVATE"] == 5


# ------------------------------------------------------- classify, real shapes
def test_batch_write_is_l2():
    """Given 6 ids (threshold is 5), When a write is classified, Then it is L2_BATCH."""
    ids = list(range(1, BATCH_THRESHOLD + 2))

    assert classify("sale.order", "write", [ids, {}], {}) == "L2_BATCH"


def test_single_write_is_l1():
    """Given 1 id, When a write is classified, Then it is L1_WRITE."""
    assert classify("sale.order", "write", [[1], {}], {}) == "L1_WRITE"


def test_write_at_the_threshold_is_still_l1():
    """Given exactly 5 ids, When a write is classified, Then it stays L1_WRITE —
    the rule is `> BATCH_THRESHOLD`, not `>=`."""
    ids = list(range(1, BATCH_THRESHOLD + 1))

    assert classify("sale.order", "write", [ids, {}], {}) == "L1_WRITE"


def test_archiving_write_is_l4():
    """Given values that set active False, When a write is classified,
    Then it is L4_DESTRUCTIVE — classification is by effect, not by method name."""
    assert classify("res.partner", "write", [[1], {"active": False}], {}) == "L4_DESTRUCTIVE"


# --------------------------------------------------------------- max_level()
def test_max_level_defaults_to_three():
    """Given no environment variable, When max_level() is read, Then it is 3."""
    assert max_level() == DEFAULT_MAX_LEVEL


def test_max_level_defaults_when_empty(monkeypatch):
    """Given an empty ceiling, When max_level() is read, Then it uses the default."""
    monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", "")

    assert max_level() == DEFAULT_MAX_LEVEL


@pytest.mark.parametrize(("raw", "expected"), [(str(level), level) for level in range(6)])
def test_max_level_reads_a_valid_ceiling(monkeypatch, raw, expected):
    """Given a numeric ODOO_MCP_MAX_LEVEL, When max_level() is read, Then it wins."""
    monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", raw)

    assert max_level() == expected


@pytest.mark.parametrize("raw", ["O", "abc", "3.5", "-1", "6"])
def test_max_level_rejects_invalid_configured_values(monkeypatch, raw):
    """Given an invalid configured ceiling, When read, Then startup is refused."""
    monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", raw)

    with pytest.raises(RuntimeError, match=rf"{re.escape(raw)}.*0.*5"):
        max_level()


# --------------------------------------------------------------------- gate()
def test_state_change_is_allowed_at_the_default_ceiling():
    """Given the default ceiling of 3, When action_confirm on 1 id is gated,
    Then it is allowed as L3_STATE_CHANGE — allowed IS the confirmed=True signal."""
    decision = gate("sale.order", "action_confirm", [1])

    assert decision.allowed is True
    assert decision.level == "L3_STATE_CHANGE"


def test_cancel_is_blocked_at_the_default_ceiling():
    """Given the default ceiling of 3, When action_cancel is gated,
    Then it is refused, and the refusal names the level and the way out."""
    decision = gate("sale.order", "action_cancel", [1])

    assert decision.allowed is False
    assert decision.level == "L4_DESTRUCTIVE"
    assert "L4_DESTRUCTIVE" in decision.reason
    assert "ODOO_MCP_MAX_LEVEL" in decision.reason
    assert "4" in decision.reason          # the value that would unblock it


def test_a_raised_ceiling_unblocks_the_destructive_call(monkeypatch):
    """Given ODOO_MCP_MAX_LEVEL=4, When unlink is gated, Then it is allowed —
    the refusal text of the previous test is actionable, not decorative."""
    monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", "4")

    decision = gate("res.partner", "unlink", [1])

    assert decision.allowed is True
    assert decision.level == "L4_DESTRUCTIVE"


def test_a_lowered_ceiling_blocks_the_state_change(monkeypatch):
    """Given ODOO_MCP_MAX_LEVEL=1, When action_confirm is gated, Then it is refused."""
    monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", "1")

    decision = gate("sale.order", "action_confirm", [1])

    assert decision.allowed is False
    assert decision.level == "L3_STATE_CHANGE"


def test_batch_write_passes_the_default_ceiling_but_single_write_survives_a_low_one(monkeypatch):
    """Given a 6-id write, When gated at 3 then at 1, Then L2_BATCH passes the
    first and fails the second — the count is what moved, not the method."""
    ids = list(range(1, BATCH_THRESHOLD + 2))

    permissive = gate("sale.order", "write", ids, {"note": "x"})
    assert permissive.allowed is True
    assert permissive.level == "L2_BATCH"

    monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", "1")
    restricted = gate("sale.order", "write", ids, {"note": "x"})
    assert restricted.allowed is False
    assert restricted.level == "L2_BATCH"
    assert gate("sale.order", "write", [1], {"note": "x"}).allowed is True


def test_archiving_write_is_blocked_at_the_default_ceiling():
    """Given values that set active False, When the write is gated,
    Then it is refused as L4_DESTRUCTIVE even though the method is `write`."""
    decision = gate("res.partner", "write", [1], {"active": False})

    assert decision.allowed is False
    assert decision.level == "L4_DESTRUCTIVE"


@pytest.mark.parametrize("ceiling", [None, "0", "3", "5", "9", "99"])
def test_private_methods_are_refused_at_every_ceiling(monkeypatch, ceiling):
    """Given any ODOO_MCP_MAX_LEVEL, When a `_`-prefixed method is gated,
    Then it is refused: Odoo rejects private methods, so no ceiling can buy them."""
    if ceiling is not None:
        monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", ceiling)

    decision = gate("account.move", "_create_invoices", [1])

    assert decision.allowed is False
    assert decision.level == "L5_PRIVATE"
    assert "ODOO_MCP_MAX_LEVEL" not in decision.reason   # raising it would not help


@pytest.mark.parametrize("ceiling", ["5", "99"])
def test_unknown_methods_are_refused_at_every_ceiling(monkeypatch, ceiling):
    """Given a method in no whitelist, When gated even above its own ordinal,
    Then it stays refused — `safe_call` rejects L5_UNKNOWN unconditionally, and a
    gate that promised otherwise would be lying to the tool layer."""
    monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", ceiling)

    decision = gate("sale.order", "action_do_whatever", [1])

    assert decision.allowed is False
    assert decision.level == "L5_UNKNOWN"
    assert "safety_layer.py" in decision.reason          # where to whitelist it


def test_an_unmapped_level_is_refused_by_default_deny(monkeypatch):
    """Given a level safety_layer.py could grow tomorrow, When gated, Then it is
    refused — an unknown severity must not become a KeyError inside a tool call,
    nor slip through because its ordinal defaulted to something permissive."""
    monkeypatch.setattr(server_safety, "classify", lambda *_a, **_kw: "L6_FUTURE")

    decision = gate("sale.order", "write", [1], {"note": "x"})

    assert decision.allowed is False
    assert decision.level == "L6_FUTURE"
    assert "LEVEL_ORDINALS" in decision.reason


def test_reads_are_allowed_at_the_floor(monkeypatch):
    """Given the strictest ceiling of 0, When a search_read is gated, Then it passes."""
    monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", "0")

    decision = gate("sale.order", "search_read", [["state", "=", "sale"]])

    assert decision.allowed is True
    assert decision.level == "L0_READ"


# ----------------------------------------------------------- structural guards
def test_account_move_without_move_type_is_refused_through_the_gate():
    """Given an account.move read with no move_type filter, When gated,
    Then the structural guard refuses it — wrapping classify() must not step
    around check_guards(), which classify() itself never calls."""
    decision = gate("account.move", "search_count", [["state", "=", "posted"]])

    assert decision.allowed is False
    assert decision.level == "L0_READ"                   # harmless level, meaningless query
    assert "move_type" in decision.reason


def test_account_move_with_move_type_passes_the_gate():
    """Given the same read WITH the filter, When gated, Then it is allowed."""
    decision = gate("account.move", "search_read", [["move_type", "=", "out_invoice"]])

    assert decision.allowed is True


def test_account_move_line_guard_reaches_through_the_related_field():
    """Given an unfiltered account.move.line read, When gated, Then it is refused
    for `move_id.move_type` — the second guarded model is wired too."""
    decision = gate("account.move.line", "search_read", [])

    assert decision.allowed is False
    assert "move_type" in decision.reason


# --------------------------------------------- the shape the wrapper must keep
def test_dict_shaped_args_silently_disable_batch_detection():
    """Given the dict-shaped args used by collaboration.py::_guard, When a 6-id
    write is classified, Then batch detection does NOT fire.

    This is the pre-existing bug, pinned here as the reason `gate()` builds a
    positional `[ids, vals]` list: a dict never reaches `args[0]`, so the count
    silently collapses to 1.
    """
    ids = list(range(1, BATCH_THRESHOLD + 2))

    assert classify("sale.order", "write", {"ids": ids}, {}) == "L1_WRITE"
    assert classify("sale.order", "write", [ids, {}], {}) == "L2_BATCH"


def test_dict_shaped_args_silently_disable_archive_detection():
    """Given the same dict shape, When an `active: False` write is classified,
    Then archiving does NOT fire (iterating a dict yields its keys, not the dict),
    while the positional shape correctly reports L4_DESTRUCTIVE."""
    assert classify("res.partner", "write", {"active": False}, {}) == "L1_WRITE"

    assert gate("res.partner", "write", [1], {"active": False}).level == "L4_DESTRUCTIVE"


def test_gate_accepts_a_bare_id_like_the_writer_does():
    """Given a single int instead of a list, When gated, Then it behaves as [id] —
    `Writer.write`/`Writer.act` normalise the same way, and a bare int reaching
    `_count_targets` unnormalised would report 1 target by accident, not by rule."""
    assert gate("sale.order", "action_confirm", 1) == gate("sale.order", "action_confirm", [1])


def test_gate_handles_a_create_with_no_ids():
    """Given a create (values, no ids), When gated, Then the values land in the
    `execute_kw` `[vals]` slot and it is L1_WRITE."""
    decision = gate("res.partner", "create", None, {"name": "ACME"})

    assert decision.allowed is True
    assert decision.level == "L1_WRITE"


def test_module_exposes_only_the_gate_surface():
    """Given the module, When its public names are listed, Then they are the three
    the tool layer is meant to use — levels are computed, never exported per tool."""
    public = {name for name in vars(server_safety) if not name.startswith("_")}

    assert {"LEVEL_ORDINALS", "GateResult", "max_level", "gate"} <= public
