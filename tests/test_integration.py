"""The suite that talks to a real Odoo. Opt-in twice, and it cleans up after
itself.

Everything here is `@pytest.mark.live`, so the default `-m "not live"` run
never reaches it. Two environment variables decide how much of it runs:

    ODOO_BASE_URL              set -> the read scenarios run
    ODOO_MCP_ALLOW_LIVE_WRITE=1  set -> the write scenarios run too

The second gate exists because a mock can prove a tool *calls* the gate, and
only a live instance can prove the gate still refuses what the real
`safety_layer.classify()` classifies. That proof costs a record, so it is not
something a stray `-m live` should trigger.

Nothing created here survives the run. A confirmed sales order cannot be
unlinked — only cancelled — so cleanup follows Odoo's own path, runs from a
`finally`, and the last statement of the write chain is the query that proves
no non-cancelled `MCP Test %` artifact is left anywhere on the instance.
"""
import json
import os
import re
import uuid
from pathlib import Path

import pytest

from odoo_assistant import server, tools_evolution, tools_read, tools_write
from odoo_assistant.odoo_scripts import explore_module as explorer
from odoo_assistant.server_errors import ToolExecutionError

from odoo_client import connect  # noqa: E402  (conftest bootstraps the path)

# The dev instance carries two companies; reading one of them and calling it
# the business is the multi-company failure this argument exists to prevent.
COMPANIES = [1, 2]
# Orders need the company that owns a sales journal.
ORDER_COMPANY = 2
TEST_PREFIX = "MCP Test"

_REPORTED_ID = re.compile(r"id=(\d+)")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("ODOO_BASE_URL"),
        reason="live: set ODOO_BASE_URL and ODOO_API_KEY to run",
    ),
]

needs_write = pytest.mark.skipif(
    os.environ.get("ODOO_MCP_ALLOW_LIVE_WRITE") != "1",
    reason="writes to a real instance: set ODOO_MCP_ALLOW_LIVE_WRITE=1 to run",
)


@pytest.fixture(scope="module")
def live_odoo():
    """The real client, reached through the server's own singleton.

    `server._credentials()` requires ODOO_DB and ODOO_USER, and the one rule
    about credentials is that neither is ever guessed: a wrong login makes
    `authenticate()` return False rather than raise, which reads like a
    permission problem. So a bootstrap client is built from the two values the
    user actually supplies, and the pair it *discovers* — the database from the
    server, the login from the key's owner — is what the server's real
    credential path then runs against.
    """
    patch = pytest.MonkeyPatch()
    bootstrap = connect(
        base=os.environ["ODOO_BASE_URL"],
        key=os.environ["ODOO_API_KEY"],
    )
    patch.setenv("ODOO_DB", str(bootstrap.db))
    patch.setenv("ODOO_USER", str(bootstrap.user))
    patch.setattr(server, "_odoo_instance", None)
    yield server._get_odoo()
    patch.undo()


@pytest.fixture(autouse=True)
def default_ceiling(monkeypatch):
    """Given: no ceiling override, so every gate below runs at the default the
    server documents (L3 allowed, destructive refused)."""
    monkeypatch.delenv("ODOO_MCP_MAX_LEVEL", raising=False)


@pytest.fixture
def scratch_home(tmp_path, monkeypatch):
    """Given: HOME is a scratch directory, so a generated reference can never
    land in the developer's real `~/.local/share/odoo-assistant/`."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Records the live value so teardown restores it after the redirect.
    monkeypatch.setattr(explorer, "REF_DIR", explorer.REF_DIR)
    tools_evolution._redirect_references()
    return Path(explorer.REF_DIR)


def _created_id(reply: str) -> int:
    """The id `create_record` reports back, or a failure naming what it said."""
    reported = _REPORTED_ID.search(reply)
    assert reported, f"create_record reported no id: {reply}"
    return int(reported.group(1))


def test_live_connection(live_odoo):
    """Given real credentials, When the server connects, Then it reaches the
    Odoo 18 series this server is verified against, as an identified user."""
    version = server._detect_version(live_odoo)
    assert str(version["serie"]).startswith("18.")
    assert version["major"] == 18
    assert live_odoo.uid > 0


def test_live_login_is_discovered_from_the_key(live_odoo):
    """Given no login at all, When a client is built from the key alone, Then
    it authenticates as the key's owner — the fact that makes ODOO_USER
    optional rather than required.

    A second client on purpose: the module fixture keeps the fast path, so an
    instance whose key owner sits at uid 60 or higher fails only this test.
    """
    keyless = connect(
        base=os.environ["ODOO_BASE_URL"],
        db=os.environ["ODOO_DB"],
        key=os.environ["ODOO_API_KEY"],
    )

    assert keyless.uid > 0
    assert keyless.user == live_odoo.user
    assert keyless.search_count("res.partner", []) > 0


def test_live_read_tools(live_odoo):
    """Given a multi-company instance, When the read tools query sale.order,
    Then the counts agree with each other and the rows are the ones asked for.

    The bounds are relative, never absolute: this instance is shared and its
    figures move, so an exact count would be a test that rots by Tuesday.
    """
    total = json.loads(tools_read.count_records("sale.order", [], company_ids=COMPANIES))
    confirmed = json.loads(
        tools_read.count_records(
            "sale.order", [["state", "=", "sale"]], company_ids=COMPANIES
        )
    )
    assert total > 0
    assert 0 < confirmed <= total

    rows = json.loads(
        tools_read.search_read(
            "sale.order",
            [["state", "=", "sale"]],
            ["name", "state", "partner_id"],
            limit=5,
            company_ids=COMPANIES,
        )
    )
    assert 0 < len(rows) <= 5
    assert {row["state"] for row in rows} == {"sale"}


def test_live_account_move_requires_move_type(live_odoo):
    """Given account.move holds four document kinds in one table, When a count
    is asked for without move_type, Then the structural guard refuses it — and
    the figure it would have returned is provably not the one on screen."""
    with pytest.raises(ToolExecutionError) as refusal:
        tools_read.count_records("account.move", [], company_ids=COMPANIES)
    assert "move_type" in str(refusal.value)

    context = {"allowed_company_ids": COMPANIES}
    mixed = live_odoo.search_count("account.move", [], context=context)
    invoices = live_odoo.search_count(
        "account.move", [["move_type", "=", "out_invoice"]], context=context
    )
    assert 0 < invoices < mixed


@needs_write
def test_live_safety_blocks_destructive_action(live_odoo):
    """Given the default ceiling, When a cancel is attempted, Then the real
    `classify()` puts it out of reach and the refusal says what would allow it.

    The id does not exist on purpose: the gate answers before Odoo is asked
    anything, so which record was named never enters into it.
    """
    with pytest.raises(ToolExecutionError) as refusal:
        tools_write.run_action("sale.order", "action_cancel", [999999999])
    reason = str(refusal.value)
    assert "L4_DESTRUCTIVE" in reason
    assert "ODOO_MCP_MAX_LEVEL=4" in reason


@needs_write
def test_live_write_chain(live_odoo, monkeypatch):
    """Given a customer that did not exist, When an order is created and
    confirmed through the tools, Then a re-read shows state 'sale' — and the
    instance is left with nothing of it that is not cancelled."""
    name = f"{TEST_PREFIX} {uuid.uuid4().hex[:8]}"
    partner_id = _created_id(
        tools_write.create_record("res.partner", {"name": name}, unique_on=["name"])
    )
    order_id = None
    try:
        order_id = _created_id(
            tools_write.create_record(
                "sale.order", {"partner_id": partner_id, "company_id": ORDER_COMPANY}
            )
        )
        tools_write.run_action("sale.order", "action_confirm", [order_id])
        # The return value is not the proof — the re-read is.
        after = json.loads(tools_read.read_record("sale.order", order_id, ["name", "state"]))
        assert after[0]["state"] == "sale"
    finally:
        # Cleanup is destructive by definition, so it runs at the ceiling that
        # permits it. Everything asserted above ran at the default one.
        monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", "4")
        if order_id:
            tools_write.cancel_record("sale.order", order_id)
        tools_write.write_record("res.partner", partner_id, {"active": False})

    # `active_test` is off so an archived partner cannot hide a live order and
    # make this pass for the wrong reason.
    left_behind = live_odoo.search_count(
        "sale.order",
        [["partner_id.name", "=like", f"{TEST_PREFIX} %"], ["state", "!=", "cancel"]],
        context={"allowed_company_ids": COMPANIES, "active_test": False},
    )
    assert left_behind == 0


def test_live_explore_module_writes_to_user_dir(live_odoo, scratch_home):
    """Given a live instance, When a reference is generated, Then a document
    built from the instance's own answers lands under the user data dir."""
    reply = tools_evolution.explore_module("helpdesk", action="generate")
    written = scratch_home / "helpdesk.md"

    assert written.is_file()
    assert str(written) in reply
    body = written.read_text()
    assert "helpdesk.ticket" in body
    assert body.count("## NOTES") == 1
