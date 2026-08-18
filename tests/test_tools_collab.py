"""Collaboration and document tools — behaviour, not implementation.

These tests drive the REAL `Documents` and `Collab` over the `MockOdoo` double
from `tests/conftest.py`. Nothing stubs those two classes: they are the verified
scripts, and a stub of them would only prove that our idea of `tell()` matches
itself. What is doubled is the boundary we own — the client — so every method
name, every keyword and every domain in the chain below is the real one.

The safety-critical case is `notify_user(subtype="comment")`: a mail.mt_comment
emails every follower, and on the reference instance 12 of 12 sampled sales
orders had a customer among theirs (references/SKILL.md rule 6). The tests that
matter most are the ones proving nothing reaches Odoo when that audience is
external and `force` was not given.
"""
import base64
import json
from datetime import date, timedelta

import pytest

from odoo_assistant.server_errors import ToolExecutionError
from odoo_assistant.tools_collab import (
    create_activity,
    download_docs,
    generate_pdf,
    notify_user,
    register,
)
from odoo_client import OdooError, OdooExecutedButUnserializable
from tests.conftest import MockOdoo

PDF_BYTES = b"%PDF-1.4 pretend invoice"
PDF_B64 = base64.b64encode(PDF_BYTES).decode()

ALICE = [5, "Alice Employee"]          # partner of user 7, an internal user
CUSTOMER = [9, "3v di veronesi e vai snc"]   # a real customer, external


class PostingOdoo(MockOdoo):
    """A double where posting actually moves the message count.

    `MockOdoo` answers a given (model, method) with ONE canned value, so
    `Documents._delivery_report`'s before/after comparison would always read
    "nothing was posted" — the tool's own success signal would be dead. Here
    the two posting methods bump the counter, which is what makes `posted`
    mean what it means against a live instance.
    """

    POSTING = ("message_notify", "message_post")

    def __init__(self):
        super().__init__()
        self.messages = 3

    def call(self, model, method, args=None, kwargs=None):
        result = super().call(model, method, args, kwargs)
        if method in self.POSTING:
            self.messages += 1
        return result

    def search_count(self, model, domain, context=None):
        if model == "mail.message":
            return self.messages
        return super().search_count(model, domain, context)


@pytest.fixture(autouse=True)
def default_ceiling(monkeypatch):
    """Given: the ceiling a host did not configure — the L3 default."""
    monkeypatch.delenv("ODOO_MCP_MAX_LEVEL", raising=False)


@pytest.fixture
def odoo(monkeypatch):
    """The injected double, with a live message count (same wiring as `mock_odoo`)."""
    from odoo_assistant import server

    client = PostingOdoo()
    monkeypatch.setattr(server, "_odoo_instance", None)
    monkeypatch.setattr(server, "_get_odoo", lambda: client)
    return client


def program_chatter(client, followers, staff=(ALICE,)):
    """Program the whole chatter round-trip: audience, post, delivery report."""
    client.set_results("mail.followers", [{"partner_id": f} for f in followers])
    client.set_results("res.users", [{"partner_id": p} for p in staff])
    client.set_results("mail.message", [
        {"id": 100, "subtype_id": [1, "Note"], "message_type": "notification"}])
    client.set_results("mail.notification", [
        {"res_partner_id": ALICE, "notification_type": "inbox",
         "notification_status": "sent"}])
    client.set_results("mail.mail", [])


def methods_called(client):
    return [c["method"] for c in client.calls]


def call_named(client, method):
    return next(c for c in client.calls if c["method"] == method)


@pytest.mark.parametrize("tool", [
    pytest.param(
        lambda: notify_user("sale.order", 42, "hello", [7]), id="notify_user"),
    pytest.param(
        lambda: create_activity("crm.lead", 11, "Call back", 7),
        id="create_activity",
    ),
    pytest.param(
        lambda: download_docs("sale.order", 42), id="download_docs"),
    pytest.param(
        lambda: generate_pdf("account.move", 5775), id="generate_pdf"),
])
def test_missing_credentials_are_mapped_for_every_collab_tool(monkeypatch, tool):
    from odoo_assistant import server
    from odoo_client import MissingCredentials

    monkeypatch.setattr(
        server, "_get_odoo",
        lambda: (_ for _ in ()).throw(MissingCredentials("missing credentials")),
    )

    with pytest.raises(ToolExecutionError) as failure:
        tool()

    assert "nothing was sent to Odoo" in str(failure.value)


# --------------------------------------------------------------- notify_user
def test_a_note_reaches_the_named_user_without_touching_followers(odoo):
    """Given a record with an external follower, When a note is sent,
    Then the post carries the user's partner and no follower is subscribed."""
    program_chatter(odoo, followers=[ALICE, CUSTOMER])
    odoo.set_results("sale.order", True, method="message_post")

    notify_user("sale.order", 42, "checked", [7])

    assert call_named(odoo, "message_post")["kwargs"]["partner_ids"] == [5]
    assert "message_subscribe" not in methods_called(odoo)


def test_a_note_reports_who_was_actually_reached(odoo):
    """Given the same note, Then the result names the audience and the delivery."""
    program_chatter(odoo, followers=[ALICE, CUSTOMER])
    odoo.set_results("sale.order", True, method="message_post")

    payload = json.loads(notify_user("sale.order", 42, "checked", [7]))

    assert payload["subtype"] == "note"
    assert payload["audience"]["external"] == [CUSTOMER[1]]
    assert payload["delivery"]["posted"] is True
    assert payload["delivery"]["notified"] == [
        {"partner": ALICE[1], "via": "inbox", "status": "sent"}]
    assert payload["delivery"]["emails_generated"] == []


def test_a_comment_is_refused_while_a_customer_follows_the_record(odoo):
    """Given an external follower, When a comment is sent without force,
    Then nothing at all is sent to Odoo."""
    program_chatter(odoo, followers=[ALICE, CUSTOMER])

    with pytest.raises(ToolExecutionError):
        notify_user("sale.order", 42, "<p>hi</p>", [7], subtype="comment")

    assert "message_post" not in methods_called(odoo)
    assert "message_notify" not in methods_called(odoo)


def test_the_refusal_names_the_customer_that_would_have_been_emailed(odoo):
    """Given the same refusal, Then the text is actionable: who, and what to do."""
    program_chatter(odoo, followers=[ALICE, CUSTOMER])

    with pytest.raises(ToolExecutionError) as refused:
        notify_user("sale.order", 42, "<p>hi</p>", [7], subtype="comment")

    text = str(refused.value)
    assert CUSTOMER[1] in text
    assert ALICE[1] not in text          # an employee is not the danger
    assert "force=True" in text and "subtype='note'" in text


def test_force_posts_the_comment_despite_the_external_follower(odoo):
    """Given force=True, When a comment is sent, Then mt_comment is posted."""
    program_chatter(odoo, followers=[ALICE, CUSTOMER])
    odoo.set_results("sale.order", True, method="message_post")

    payload = json.loads(notify_user("sale.order", 42, "<p>hi</p>", [7],
                                     subtype="comment", force=True))

    posted = call_named(odoo, "message_post")
    assert posted["kwargs"]["subtype_xmlid"] == "mail.mt_comment"
    assert payload["audience"]["external"] == [CUSTOMER[1]]


def test_a_comment_needs_no_force_when_every_follower_is_an_employee(odoo):
    """Given only internal followers, When a comment is sent, Then it goes out."""
    program_chatter(odoo, followers=[ALICE])
    odoo.set_results("sale.order", True, method="message_post")

    payload = json.loads(notify_user("sale.order", 42, "<p>hi</p>", [7],
                                     subtype="comment"))

    assert payload["audience"]["external"] == []
    assert "message_post" in methods_called(odoo)


def test_an_unknown_subtype_posts_nothing(odoo):
    """Given a subtype nobody defined, When it is used, Then no post is attempted."""
    program_chatter(odoo, followers=[ALICE])

    with pytest.raises(ToolExecutionError) as refused:
        notify_user("sale.order", 42, "<p>hi</p>", [7], subtype="internal")

    assert "internal" in str(refused.value)
    assert "UNCERTAIN" not in str(refused.value)
    assert odoo.calls == []


def test_a_read_only_ceiling_refuses_even_the_safe_note(odoo, monkeypatch):
    """Given ODOO_MCP_MAX_LEVEL=0, When a note is sent, Then it is refused as L1."""
    monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", "0")
    program_chatter(odoo, followers=[ALICE])

    with pytest.raises(ToolExecutionError) as refused:
        notify_user("sale.order", 42, "<p>hi</p>", [7])

    assert "L1_WRITE" in str(refused.value)
    assert "message_notify" not in methods_called(odoo)


@pytest.mark.parametrize(("ceiling", "run", "level"), [
    ("0", lambda: notify_user("sale.order", 42, "<p>hi</p>", [7]), "L1_WRITE"),
    ("0", lambda: create_activity("crm.lead", 11, "Call back", 7), "L1_WRITE"),
    ("2", lambda: generate_pdf("account.move", 5775, "/tmp"), "L3_STATE_CHANGE"),
], ids=["notify_user", "create_activity", "generate_pdf"])
def test_a_blocked_gate_refuses_in_its_own_words_and_opens_no_connection(
        odoo, monkeypatch, ceiling, run, level):
    """Given a ceiling that blocks the tool, When it runs, Then the gate's own
    reason is what the caller reads and the client was never used.

    Both halves matter. A refusal re-mapped to "UNCERTAIN: may or may not have
    been applied" turns "nothing was touched" into "something might have been",
    and an empty call log is the only proof that a blocked server really is a
    blocked server — `notify_user` used to read the audience before deciding.
    """
    monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", ceiling)
    program_chatter(odoo, followers=[ALICE, CUSTOMER])

    with pytest.raises(ToolExecutionError) as refused:
        run()

    assert level in str(refused.value)
    assert "UNCERTAIN" not in str(refused.value)
    assert odoo.calls == []


# ------------------------------------------------------------ create_activity
def program_activity(client, types, created=(77,)):
    client.set_results("mail.activity.type", list(types))
    client.set_results("ir.model", [{"id": 55}])
    client.set_results("mail.activity", list(created), method="create")
    client.set_results("mail.activity", [
        {"id": 77, "summary": "Call back", "user_id": [7, "Alice Employee"],
         "date_deadline": "2026-08-16", "state": "planned",
         "activity_type_id": [4, "To Do"], "res_model": "crm.lead",
         "res_id": 11}])


def test_create_activity_writes_the_deadline_and_the_assignee(odoo):
    """Given days=3, When an activity is created, Then the deadline is today+3."""
    program_activity(odoo, types=[{"id": 4, "name": "To Do"}])

    payload = json.loads(create_activity("crm.lead", 11, "Call back", 7, days=3))

    vals = call_named(odoo, "create")["args"][0]
    assert vals["date_deadline"] == str(date.today() + timedelta(days=3))
    assert vals["user_id"] == 7
    assert vals["res_model_id"] == 55 and vals["res_model"] == "crm.lead"
    assert payload["summary"] == "Call back"


def test_create_activity_resolves_the_type_name_against_the_instance(odoo):
    """Given this instance's own types, When "call" is asked for, Then its id is used."""
    program_activity(odoo, types=[{"id": 4, "name": "To Do"},
                                  {"id": 9, "name": "Call"}])

    create_activity("crm.lead", 11, "Call back", 7, activity_type="call")

    assert call_named(odoo, "create")["args"][0]["activity_type_id"] == 9


def test_create_activity_reports_a_committed_write_without_retrying(odoo):
    """Given a create that commits then fails to serialise, When it is called,
    Then the result is a success that re-reads and forbids a retry."""
    program_activity(odoo, types=[{"id": 4, "name": "To Do"}])
    odoo.set_results("mail.activity",
                     OdooExecutedButUnserializable("cannot marshal None"),
                     method="create")

    text = create_activity("crm.lead", 11, "Call back", 7)

    assert "COMMITTED" in text and "Do NOT retry" in text
    assert "Call back" in text          # the state was actually re-read
    assert len([c for c in odoo.calls if c["method"] == "create"]) == 1


class VerificationFailsOdoo(PostingOdoo):
    """A double where the create lands and the read that VERIFIES it fails.

    That read is a SEPARATE call: `Collab.todo` creates the activity and then
    re-reads it (collaboration.py:135-138). An OdooError arriving from the
    second call arrives with the record already in the database — which is why
    the phase, not the exception class, decides what may be claimed.
    """

    def __init__(self):
        super().__init__()
        self.activity_reads = 0

    def call(self, model, method, args=None, kwargs=None):
        if model == "mail.activity" and method == "search_read":
            self.activity_reads += 1
            if self.activity_reads == 1:
                raise OdooError("mail.activity.search_read: cannot marshal None")
        return super().call(model, method, args, kwargs)


@pytest.fixture
def verification_fails(monkeypatch):
    """Given: the activity is created, then the read that confirms it fails."""
    from odoo_assistant import server

    client = VerificationFailsOdoo()
    monkeypatch.setattr(server, "_odoo_instance", None)
    monkeypatch.setattr(server, "_get_odoo", lambda: client)
    program_activity(client, types=[{"id": 4, "name": "To Do"}])
    return client


def test_a_failed_verification_never_reports_the_activity_as_not_created(
        verification_fails):
    """Given the create landed and its verification read failed, When the tool
    answers, Then it does NOT say nothing changed.

    It did change: the activity is in the database. Telling the caller otherwise
    is the invitation to call again, and calling again is a second activity.
    """
    with pytest.raises(ToolExecutionError) as failure:
        create_activity("crm.lead", 11, "Call back", 7)

    assert "nothing changed" not in str(failure.value)
    assert "repeating the same call cannot succeed" not in str(failure.value)
    assert "UNCERTAIN" in str(failure.value)


def test_a_failed_verification_answers_with_the_activity_it_re_read(
        verification_fails):
    """Given the same failure, Then the answer is what the re-read found."""
    with pytest.raises(ToolExecutionError) as failure:
        create_activity("crm.lead", 11, "Call back", 7)

    assert "Call back" in str(failure.value)
    assert verification_fails.activity_reads == 2      # verify, then re-read
    assert len([c for c in verification_fails.calls if c["method"] == "create"]) == 1


# -------------------------------------------------------------- download_docs
def test_download_docs_saves_the_attachment_it_can_read(odoo, tmp_path):
    """Given one readable attachment, When it is downloaded, Then it hits disk."""
    odoo.set_results("ir.attachment", [
        {"id": 1, "name": "quote.pdf", "mimetype": "application/pdf",
         "file_size": 24, "create_date": "2026-08-01", "type": "binary"}])
    odoo.set_results("ir.attachment", [
        {"name": "quote.pdf", "datas": PDF_B64, "mimetype": "application/pdf",
         "store_fname": "a/b", "file_size": 24}], method="read")
    odoo.set_results("sale.order", {"name": {"type": "char"}}, method="fields_get")

    payload = json.loads(download_docs("sale.order", 42, str(tmp_path)))

    assert payload["skipped"] == []
    assert (tmp_path / "quote.pdf").read_bytes() == PDF_BYTES
    assert payload["saved"] == [str(tmp_path / "quote.pdf")]


def test_download_docs_reports_the_file_whose_bytes_are_gone(odoo, tmp_path):
    """Given an attachment restored without its filestore, When it is downloaded,
    Then it is named in `skipped` instead of silently missing from `saved`."""
    odoo.set_results("ir.attachment", [
        {"id": 1, "name": "lost.pdf", "mimetype": "application/pdf",
         "file_size": 8400, "create_date": "2026-08-01", "type": "binary"}])
    odoo.set_results("ir.attachment", [
        {"name": "lost.pdf", "datas": False, "mimetype": "application/pdf",
         "store_fname": "a/b", "file_size": 8400}], method="read")
    odoo.set_results("sale.order", {"name": {"type": "char"}}, method="fields_get")

    payload = json.loads(download_docs("sale.order", 42, str(tmp_path)))

    assert payload["saved"] == []
    assert payload["skipped"][0][0] == "lost.pdf"
    assert "filestore" in payload["skipped"][0][1]


def test_download_docs_is_not_blocked_by_the_move_type_guard(odoo, tmp_path):
    """Given an invoice, When its documents are downloaded, Then the account.move
    guard does not fire: the gate is asked about the ir.attachment query, and
    `gate("account.move", "read", [id])` would refuse an id list that carries no
    domain to filter."""
    odoo.set_results("ir.attachment", [
        {"id": 1, "name": "FT_2026_0062.pdf", "mimetype": "application/pdf",
         "file_size": 24, "create_date": "2026-08-01", "type": "binary"}])
    odoo.set_results("ir.attachment", [
        {"name": "FT_2026_0062.pdf", "datas": PDF_B64,
         "mimetype": "application/pdf", "store_fname": "a/b", "file_size": 24}],
        method="read")
    odoo.set_results("account.move", {"name": {"type": "char"}},
                     method="fields_get")

    payload = json.loads(download_docs("account.move", 5775, str(tmp_path)))

    assert payload["saved"] == [str(tmp_path / "FT_2026_0062.pdf")]


# ---------------------------------------------------------------- generate_pdf
def test_generate_pdf_returns_the_path_it_wrote(odoo, tmp_path):
    """Given an already rendered PDF, When it is generated, Then no wizard runs."""
    odoo.set_results("ir.attachment", [
        {"id": 1, "name": "FT_2026_0062.pdf", "mimetype": "application/pdf",
         "file_size": 24, "create_date": "2026-08-01", "type": "binary"}])
    odoo.set_results("ir.attachment", [
        {"name": "FT_2026_0062.pdf", "datas": PDF_B64,
         "mimetype": "application/pdf", "store_fname": "a/b", "file_size": 24}],
        method="read")

    payload = json.loads(generate_pdf("account.move", 5775, str(tmp_path)))

    assert payload["path"] == str(tmp_path / "FT_2026_0062.pdf")
    assert (tmp_path / "FT_2026_0062.pdf").read_bytes() == PDF_BYTES
    assert "action_send_and_print" not in methods_called(odoo)


def test_generate_pdf_is_refused_below_the_state_change_ceiling(odoo, tmp_path,
                                                                monkeypatch):
    """Given ODOO_MCP_MAX_LEVEL=2, When a PDF is generated, Then it is refused as
    L3 — the print wizard can also SEND the document — and nothing is read."""
    monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", "2")

    with pytest.raises(ToolExecutionError) as refused:
        generate_pdf("account.move", 5775, str(tmp_path))

    assert "L3_STATE_CHANGE" in str(refused.value)
    assert "ODOO_MCP_MAX_LEVEL=3" in str(refused.value)
    assert odoo.calls == []


# -------------------------------------------------------------------- register
def test_register_publishes_the_four_tools():
    """Given a bare server, When register() runs, Then the four tools are on it."""
    import anyio
    from mcp.server import MCPServer

    mcp = MCPServer("test-collab")
    register(mcp)

    assert {t.name for t in anyio.run(mcp.list_tools)} == {
        "notify_user", "create_activity", "download_docs", "generate_pdf"}


def test_a_note_is_posted_as_an_internal_chatter_note(odoo):
    """Given a note, When it is sent, Then it is posted with `mail.mt_note` so
    it is VISIBLE in the record's chatter.

    `message_notify` — what this used to call — creates a `user_notification`,
    and Odoo's own docstring for it says it is for "messages that should not
    be displayed on a document". The note landed nowhere the user could see it
    and the tool reported success. `mt_note` is internal-only: measured on a
    real order it produced one inbox notification and zero customer emails.
    """
    program_chatter(odoo, followers=[ALICE, CUSTOMER])
    odoo.set_results("sale.order", True, method="message_post")

    notify_user("sale.order", 42, "checked", [7])

    posted = call_named(odoo, "message_post")
    assert posted["kwargs"]["subtype_xmlid"] == "mail.mt_note"
    assert posted["kwargs"]["message_type"] == "comment"
    assert posted["kwargs"]["partner_ids"] == [5]
    assert "message_notify" not in methods_called(odoo)


def test_the_inbox_subtype_notifies_without_writing_on_the_record(odoo):
    """Given a message meant for a person rather than for the record, When
    subtype='inbox' is used, Then it goes through message_notify and leaves
    the chatter untouched.

    This is the case `note` used to serve by accident: Odoo's own docstring
    for `message_notify` calls it the path for "messages that should not be
    displayed on a document". Naming it makes the invisibility a choice
    instead of a surprise.
    """
    program_chatter(odoo, followers=[ALICE, CUSTOMER])
    odoo.set_results("sale.order", True, method="message_notify")

    payload = json.loads(
        notify_user("sale.order", 42, "ping", [7], subtype="inbox"))

    assert call_named(odoo, "message_notify")["kwargs"]["partner_ids"] == [5]
    assert "message_post" not in methods_called(odoo)
    assert payload["subtype"] == "inbox"
