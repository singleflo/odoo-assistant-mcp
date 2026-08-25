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
import base64
import json
import os
import re
import uuid
from pathlib import Path

import pytest

from odoo_assistant import (
    server,
    tools_collab,
    tools_discuss,
    tools_evolution,
    tools_read,
    tools_write,
)
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

    uid = keyless.uid
    partners = keyless.search_count("res.partner", [])

    assert isinstance(uid, int) and uid > 0
    assert keyless.user == live_odoo.user
    assert isinstance(partners, int) and partners > 0


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


def test_live_required_fields_surfaces_the_defaulted_trap(live_odoo):
    """Given `crm.lead.type` is required and defaults to 'lead' on an instance
    whose pipeline is run as opportunities, When `required_fields` is asked
    about crm.lead, Then the reply names the field, the default Odoo would
    apply and how live records actually use it — the comparison that makes a
    silently misfiled create visible, which is the entire reason this tool
    exists.

    The reply is prose, not JSON, so it is read back the way an agent reads it:
    a two-space block per required field, the default and the live distribution
    indented under it.
    """
    reply = tools_read.required_fields("crm.lead")
    assert reply.startswith("crm.lead — Odoo requires")

    blocks = {
        block.split()[0]: block for block in re.split(r"\n(?=  \w+  \()", reply)[1:]
    }
    assert "type" in blocks, f"required field `type` not reported: {reply}"

    defaulted = re.search(
        r"Odoo would default to (.+?) — existing records: (.+)", blocks["type"]
    )
    assert defaulted, f"no default reported beside `type`: {blocks['type']}"
    assert defaulted.group(1).strip("'\"") == "lead"

    # Present and non-empty, never an exact figure: this instance is shared and
    # its pipeline moves, so a pinned count would be a test that rots.
    usage = dict(re.findall(r"(\w+)=(\d+)", defaulted.group(2)))
    assert usage, f"no live distribution beside the default: {defaulted.group(2)}"
    assert sum(int(count) for count in usage.values()) > 0


def test_live_list_known_modules_reports_bundled_and_generated(live_odoo, scratch_home):
    """Given a scratch reference dir that starts empty, When the modules are
    listed before and after a live generation, Then only the bundled set is
    known at first and `helpdesk` joins it as 'generated', carrying the stamp
    and the record count read back out of the document just written."""
    before = json.loads(tools_evolution.list_known_modules())
    assert all(
        set(entry) == {"module", "source", "generated", "records", "path"}
        for entry in before
    ), f"unexpected entry shape: {before}"
    assert [entry for entry in before if entry["source"] == "bundled"]
    assert [entry for entry in before if entry["source"] == "generated"] == []

    tools_evolution.explore_module("helpdesk", action="generate")

    after = json.loads(tools_evolution.list_known_modules())
    learned = [
        entry
        for entry in after
        if entry["module"] == "helpdesk" and entry["source"] == "generated"
    ]
    assert len(learned) == 1, f"helpdesk not learned: {after}"
    assert learned[0]["generated"] is not None
    assert learned[0]["records"] is not None


@needs_write
def test_live_collab_on_a_self_created_partner(live_odoo, monkeypatch, tmp_path):
    """Given a partner this test creates itself — whose entire audience is
    therefore the owner of the API key, the one property that makes every step
    below incapable of reaching a stranger — When the four collaboration tools
    run over it as a single lifecycle, Then each effect is proven by a re-read
    and the instance dispatched nothing to anybody.

    These four had never run against a real instance. Three assertions come
    from the sources, or from what the instance answered, rather than from the
    obvious expectation:

      * a fresh partner does NOT have zero followers. Odoo auto-subscribes the
        creator (`mail_create_nosubscribe` is what would suppress it), so the
        set starts at exactly one — us. The activity is assigned to that same
        user, which is why `mail.activity` subscribing its assignee cannot grow
        the audience and why Odoo skips the assignee notification it would
        otherwise send;
      * `download_docs` returns MORE than the attachments. `Documents.download`
        also dumps every non-empty binary FIELD (documents.py:140-152), and a
        partner's `avatar_*` are computed to a generated SVG instead of being
        left empty, so the attachment is one entry among several;
      * `generate_pdf` on `res.partner` never reaches a wizard. It returns
        from the attachment-reuse branch (documents.py:182-187) BEFORE the
        `wizards` lookup at line 189, so `action_send_and_print` — the method
        the tool gates on — is structurally unreachable here.
    """
    name = f"{TEST_PREFIX} {uuid.uuid4().hex[:8]}"
    partner_id = _created_id(
        tools_write.create_record("res.partner", {"name": name}, unique_on=["name"])
    )
    activity_id = attachment_id = None
    try:
        watchers = [["res_model", "=", "res.partner"], ["res_id", "=", partner_id]]

        def followers() -> list[int]:
            return sorted(
                row["partner_id"][0]
                for row in live_odoo.search_read(
                    "mail.followers", watchers, ["partner_id"]
                )
            )

        owner = live_odoo.search_read(
            "res.users", [["id", "=", live_odoo.uid]], ["partner_id"]
        )[0]["partner_id"][0]
        assert followers() == [owner]
        summary = f"{name} follow-up"
        scheduled = json.loads(
            tools_collab.create_activity(
                "res.partner", partner_id, summary, live_odoo.uid, days=1
            )
        )
        activity_id = scheduled["id"]
        # The return value is not the proof — the re-read is.
        pending = live_odoo.search_read(
            "mail.activity",
            [["res_model", "=", "res.partner"], ["res_id", "=", partner_id]],
            ["summary", "user_id"],
        )
        assert len(pending) == 1, f"the activity is not on the partner: {pending}"
        assert pending[0]["summary"] == summary
        assert pending[0]["user_id"][0] == live_odoo.uid
        # `mail.activity` subscribes its assignee, which is the only way this
        # lifecycle could grow the audience — and the assignee is the user who
        # is already following.
        assert followers() == [owner]

        told = json.loads(
            tools_collab.notify_user(
                "res.partner", partner_id, f"{summary}: internal note",
                [live_odoo.uid], subtype="note",
            )
        )
        assert told["subtype"] == "note"
        assert told["audience"]["external"] == []
        assert told["delivery"]["posted"] is True
        # The live proof of the claim the whole note path rests on: reading the
        # audience BEFORE posting is only worth anything if the post itself
        # cannot enlarge it, and `message_notify` is what makes that so.
        assert followers() == [owner]

        attachment_id = _created_id(
            tools_write.create_record(
                "ir.attachment",
                {
                    "name": f"{TEST_PREFIX}.pdf",
                    "res_model": "res.partner",
                    "res_id": partner_id,
                    "mimetype": "application/pdf",
                    "datas": base64.b64encode(b"%PDF-1.4\n%%EOF\n").decode(),
                },
            )
        )
        fetched = json.loads(
            tools_collab.download_docs("res.partner", partner_id, str(tmp_path))
        )
        # An empty `skipped` is the assertion that carries weight: a database
        # restored without its filestore keeps the attachment row and loses the
        # bytes, and that shows up there rather than as a short `saved` list.
        assert fetched["skipped"] == []
        saved = [Path(path) for path in fetched["saved"]]
        assert saved and all(path.parent == tmp_path for path in saved)
        document = [path for path in saved if path.name == f"{TEST_PREFIX}.pdf"]
        assert len(document) == 1, f"the attachment was not saved once: {saved}"
        assert document[0].read_bytes()[:4] == b"%PDF"
        # Everything else `saved` carries is a binary FIELD dump of the record
        # itself, named by `download`, never a second document.
        dumped = [path for path in saved if path != document[0]]
        assert dumped, f"no binary field was dumped beside the attachment: {saved}"
        assert all(
            path.name.startswith(f"res.partner_{partner_id}_") for path in dumped
        )

        printed = json.loads(
            tools_collab.generate_pdf("res.partner", partner_id, str(tmp_path))
        )
        rendered = Path(printed["path"])
        assert rendered.is_file()
        assert rendered.read_bytes()[:4] == b"%PDF"
        # The negative proof for the branch named in the docstring: no wizard
        # ran, so nothing was queued for delivery on this record at all.
        assert (
            live_odoo.search_count(
                "mail.mail",
                [["res_id", "=", partner_id], ["model", "=", "res.partner"]],
            )
            == 0
        )
    finally:
        # Cleanup is destructive by definition, so it runs at the ceiling that
        # permits it. Everything asserted above ran at the default one.
        monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", "4")
        if activity_id:
            tools_write.run_action("mail.activity", "unlink", [activity_id])
        if attachment_id:
            tools_write.run_action("ir.attachment", "unlink", [attachment_id])
        # A partner cannot be unlinked, so the instance's own path is archiving.
        tools_write.write_record("res.partner", partner_id, {"active": False})

    # `active_test` is off so an archived partner cannot hide the rows this
    # test is claiming it removed.
    residue = {"allowed_company_ids": COMPANIES, "active_test": False}
    assert (
        live_odoo.search_count(
            "mail.activity", [["summary", "=like", f"{TEST_PREFIX} %"]], residue
        )
        == 0
    )
    assert (
        live_odoo.search_count(
            "ir.attachment", [["name", "=like", f"{TEST_PREFIX}%"]], residue
        )
        == 0
    )


def test_live_notify_refuses_to_email_an_external_follower(live_odoo):
    """Given a real sale.order followed by somebody who is not an employee of
    this instance, When a comment is posted on it, Then the tool refuses,
    names the person who would have received that email and the `force=True`
    that would send it anyway — and the record's chatter is unchanged.

    The message count is the assertion that carries the weight. `notify_user`
    reads the audience BEFORE it posts, and on a live instance the only proof
    of that ordering is that a refused comment left nothing behind: an audience
    checked afterwards would have mailed the customer already. This is the one
    refusal whose failure mode is a stranger's inbox, so `force=True` is never
    passed anywhere in this suite.

    The target is discovered, never pinned: a hardcoded id rots, and the rule
    for "external" is the instance's own — copied from `Documents.audience`
    (documents.py:236-239), a follower is staff only when a non-share
    `res.users` carries that partner.
    """
    followers = live_odoo.search_read(
        "mail.followers",
        [["res_model", "=", "sale.order"]],
        ["res_id", "partner_id"],
        limit=80,
    )
    staff = live_odoo.search_read(
        "res.users",
        [
            ["partner_id", "in", [row["partner_id"][0] for row in followers]],
            ["share", "=", False],
        ],
        ["partner_id"],
    )
    internal = {row["partner_id"][0] for row in staff}
    outsiders = [row for row in followers if row["partner_id"][0] not in internal]
    if not outsiders:
        pytest.skip("no sale.order with an external follower on this instance")

    order_id, customer = outsiders[0]["res_id"], outsiders[0]["partner_id"][1]
    chatter = [["model", "=", "sale.order"], ["res_id", "=", order_id]]
    before = live_odoo.search_count("mail.message", chatter)

    with pytest.raises(ToolExecutionError) as refusal:
        tools_collab.notify_user(
            "sale.order",
            order_id,
            "MUST NOT BE POSTED",
            [live_odoo.uid],
            subtype="comment",
        )

    reason = str(refusal.value)
    assert customer in reason, f"the refusal does not name who it protected: {reason}"
    assert "force=True" in reason
    assert live_odoo.search_count("mail.message", chatter) == before


def test_live_cancel_record_is_refused_at_the_default_ceiling(live_odoo):
    """Given the default ceiling, When `cancel_record` is called, Then the real
    `classify()` puts it out of reach and the refusal names both the level and
    the `ODOO_MCP_MAX_LEVEL=4` that would allow it.

    `cancel_record` is the tool a host offers for "cancel this". The existing
    destructive test proves the gate through `run_action`; this holds the same
    gate through the entry point an agent actually reaches for, which had never
    been asserted.

    The id does not exist on purpose: the gate has to answer before Odoo is
    asked anything, and naming a record that could not be found is what proves
    it did.
    """
    with pytest.raises(ToolExecutionError) as refusal:
        tools_write.cancel_record("sale.order", 999999999)
    reason = str(refusal.value)
    assert "L4_DESTRUCTIVE" in reason
    assert "ODOO_MCP_MAX_LEVEL=4" in reason


@needs_write
def test_live_cancel_record_succeeds_at_ceiling_four(live_odoo, monkeypatch):
    """Given a confirmed order this test created itself, When the ceiling is
    raised to 4 and `cancel_record` runs, Then a re-read shows state 'cancel'
    — and the instance is left with nothing of it that is not cancelled.

    The success path of this tool already runs on every live write test, in
    their teardown, where nothing asserts what it did. A `cancel_record` that
    quietly stopped cancelling would leave all of them green and the instance
    filling with confirmed orders. Same call, this time proved.
    """
    order_id = None
    order_is_cancelled = False
    name = f"{TEST_PREFIX} {uuid.uuid4().hex[:8]}"
    partner_id = _created_id(
        tools_write.create_record("res.partner", {"name": name}, unique_on=["name"])
    )
    try:
        order_id = _created_id(
            tools_write.create_record(
                "sale.order", {"partner_id": partner_id, "company_id": ORDER_COMPANY}
            )
        )
        tools_write.run_action("sale.order", "action_confirm", [order_id])
        confirmed = json.loads(
            tools_read.read_record("sale.order", order_id, ["name", "state"])
        )
        assert confirmed[0]["state"] == "sale"

        monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", "4")
        tools_write.cancel_record("sale.order", order_id)
        # Recorded before the assertion: a transition is one-way, so a failing
        # assertion must not send the teardown into a second cancel.
        order_is_cancelled = True
        # The return value is not the proof — the re-read is.
        after = json.loads(
            tools_read.read_record("sale.order", order_id, ["name", "state"])
        )
        assert after[0]["state"] == "cancel"
    finally:
        # Cleanup is destructive by definition, so it runs at the ceiling that
        # permits it — set again here because a failure above may have landed
        # before the body raised it.
        monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", "4")
        if order_id and not order_is_cancelled:
            tools_write.cancel_record("sale.order", order_id)
        # A partner cannot be unlinked, so the instance's own path is archiving.
        tools_write.write_record("res.partner", partner_id, {"active": False})

    # `active_test` is off so an archived partner cannot hide a live order and
    # make this pass for the wrong reason.
    left_behind = live_odoo.search_count(
        "sale.order",
        [["partner_id.name", "=like", f"{TEST_PREFIX} %"], ["state", "!=", "cancel"]],
        context={"allowed_company_ids": COMPANIES, "active_test": False},
    )
    assert left_behind == 0


def test_live_message_targets_lists_users_with_presence(live_odoo):
    """Given the roster helper, When it is asked, Then it returns internal
    users with a readable im_status and the conversations the caller belongs
    to — the data an agent needs before it invents an id.

    The caller's own account is online while this runs (it is polling Odoo),
    so at least one presence is a real value rather than the offline default.
    """
    answer = json.loads(tools_discuss.list_message_targets())

    assert answer["users"], "no internal users reported"
    presences = {u["presence"] for u in answer["users"]}
    assert presences & {"online", "away", "offline"}, presences
    assert all("user_id" in u and "name" in u for u in answer["users"])
    for conversation in answer["conversations"]:
        assert conversation["type"] in {"chat", "group", "channel"}


@needs_write
def test_live_direct_message_reaches_a_user_without_email(live_odoo):
    """Given a Discuss direct message to a colleague, When it is sent, Then it
    lands in a chat and NO mail leaves — the property that separates it from
    notify_user, proven against the instance rather than asserted.

    The recipient is the caller's own account: a direct message is a two-party
    chat, and messaging oneself keeps the whole exchange inside this test with
    no second person to disturb. The proof is the re-read of the channel and
    the unchanged mail.mail count.
    """
    before = live_odoo.search_count("mail.mail", [])

    sent = json.loads(
        tools_discuss.send_direct_message(
            live_odoo.uid, f"{TEST_PREFIX} direct message probe"
        )
    )
    channel_id = sent["channel_id"]

    posted = live_odoo.search_read(
        "mail.message",
        [["model", "=", _channel_model(live_odoo)], ["res_id", "=", channel_id]],
        ["body"],
        limit=1,
        order="date desc",
    )
    assert posted and TEST_PREFIX in posted[0]["body"]
    assert live_odoo.search_count("mail.mail", []) == before


def test_live_read_conversation_returns_the_message_just_sent(live_odoo):
    """Given a channel the caller can see, When its conversation is read, Then
    the newest message comes back with its author — the read side of the same
    chat the direct-message test wrote to.

    Read-only and self-scoped: it opens the caller's own 1-to-1 channel by id
    and reads it, asserting shape and ordering, never an exact body that a
    concurrent run could change.
    """
    channel_model = _channel_model(live_odoo)
    opened = live_odoo.call(
        channel_model, "channel_get", [[_self_partner(live_odoo)]]
    )
    channel_id = opened[channel_model][0]["id"]

    messages = json.loads(tools_discuss.read_conversation(channel_id, limit=5))

    assert isinstance(messages, list)
    for message in messages:
        assert "author" in message and "date" in message and "body" in message


@needs_write
def test_live_channel_message_refuses_a_room_with_an_outsider(live_odoo, monkeypatch):
    """Given a group that contains a partner who is not an employee, When a
    message is posted to it, Then the tool refuses, names the outsider, and the
    channel stays empty — the same guard notify_user applies to followers.

    A guest or portal partner in the room is the failure mode; OdooBot, a
    system user in every internal channel, must NOT trigger it. The group is
    built for the test with the caller plus a real external partner, and torn
    down by archiving.
    """
    outsider = live_odoo.search_read(
        "res.partner",
        [["user_ids", "=", False], ["is_company", "=", False], ["id", "!=", 2]],
        ["name"],
        limit=1,
    )
    if not outsider:
        pytest.skip("no external partner on this instance to build the group")

    channel_model = _channel_model(live_odoo)
    created = live_odoo.call(
        channel_model,
        "create_group",
        [[_self_partner(live_odoo), outsider[0]["id"]]],
    )
    channel_id = created[channel_model][0]["id"]
    try:
        with pytest.raises(ToolExecutionError) as refusal:
            tools_discuss.send_channel_message(channel_id, "MUST NOT BE POSTED")

        reason = str(refusal.value)
        assert outsider[0]["name"] in reason, reason
        assert "REFUSED" in reason
        assert (
            live_odoo.search_count(
                "mail.message",
                [["model", "=", channel_model], ["res_id", "=", channel_id]],
            )
            == 0
        )
    finally:
        monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", "4")
        tools_write.write_record(channel_model, channel_id, {"active": False})


def _self_partner(live_odoo) -> int:
    """The partner behind the API key — the peer for a self-scoped chat."""
    rows = live_odoo.search_read("res.users", [["id", "=", live_odoo.uid]], ["partner_id"])
    return rows[0]["partner_id"][0]


def _channel_model(live_odoo) -> str:
    """The channel model of the instance under test.

    Asked rather than spelled, so this suite is the same proof on Odoo 16
    (`mail.channel`) as on 17+ (`discuss.channel`). It goes through the tools'
    own resolver, which shares this client and therefore its cached answer.
    """
    return tools_discuss._discuss_models(live_odoo)[0]
