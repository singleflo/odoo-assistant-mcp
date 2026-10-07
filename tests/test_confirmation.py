"""Outbound messages wait for the user's approval.

Anthropic's directory review asked for it: `send_direct_message`,
`send_channel_message` and `notify_user` speak for the user, and the user must
see and approve each message before it leaves. The first call previews and
sends nothing; only a second call carrying the preview's token sends, and the
token approves exactly the previewed arguments, once.
"""
import json

import pytest

from odoo_assistant import confirmation, tenant
from odoo_assistant.server_errors import ToolExecutionError
from odoo_assistant.tools_collab import notify_user
from odoo_assistant.tools_discuss import send_channel_message, send_direct_message

SENDING = {"channel_get", "message_post", "message_notify"}


@pytest.fixture(autouse=True)
def default_lists(monkeypatch):
    monkeypatch.delenv("ODOO_MCP_ALLOW", raising=False)
    monkeypatch.delenv("ODOO_MCP_DENY", raising=False)
    monkeypatch.delenv("ODOO_MCP_ALLOW_UNLINK", raising=False)


@pytest.fixture
def ada(mock_odoo):
    """A recipient a direct message can reach, on a post-17 instance."""
    mock_odoo.set_results("ir.model", 1, method="search_count")
    mock_odoo.set_results("res.users", [
        {"id": 9, "name": "Ada", "partner_id": [12, "Ada"], "share": False}])
    mock_odoo.set_results(
        "discuss.channel", {"discuss.channel": [{"id": 44}]}, method="channel_get")
    mock_odoo.set_results("discuss.channel", 91, method="message_post")
    return mock_odoo


@pytest.fixture
def team(mock_odoo):
    """An all-employee channel of three members."""
    mock_odoo.set_results("ir.model", 1, method="search_count")
    mock_odoo.set_results("discuss.channel", [
        {"id": 44, "name": "Team", "channel_type": "channel", "member_count": 3}])
    mock_odoo.set_results("discuss.channel.member", [
        {"partner_id": [12, "Ada"], "guest_id": False}])
    mock_odoo.set_results("res.users", [{"partner_id": [12, "Ada"]}])
    mock_odoo.set_results("ir.model.data", [])
    mock_odoo.set_results("discuss.channel", 92, method="message_post")
    return mock_odoo


@pytest.fixture
def order_with_customer(mock_odoo):
    """A sales order followed by an employee and by a customer."""
    mock_odoo.set_results("mail.followers", [
        {"partner_id": [5, "Alice Employee"]}, {"partner_id": [9, "Customer"]}])
    mock_odoo.set_results("res.users", [{"partner_id": [5, "Alice Employee"]}])
    return mock_odoo


def sent(client):
    return [c["method"] for c in client.calls if c["method"] in SENDING]


def test_a_direct_message_preview_sends_nothing_and_opens_no_chat(ada):
    """`channel_get` CREATES the private chat when none exists, so the
    preview must stop before it, not only before the post."""
    preview = json.loads(send_direct_message(9, "Report is ready"))

    assert preview["status"].startswith("NOT SENT")
    assert preview["preview"]["recipient"] == "Ada"
    assert preview["preview"]["message"] == "Report is ready"
    assert preview["confirmation_token"]
    assert sent(ada) == []


def test_the_approved_token_sends_the_previewed_message(ada):
    token = json.loads(send_direct_message(9, "Report is ready"))["confirmation_token"]

    result = json.loads(send_direct_message(9, "Report is ready", confirmation_token=token))

    assert result["message_id"] == 91
    assert sent(ada) == ["channel_get", "message_post"]


def test_a_token_never_sends_a_different_message_than_the_one_previewed(ada):
    token = json.loads(send_direct_message(9, "Report is ready"))["confirmation_token"]

    with pytest.raises(ToolExecutionError, match="different arguments"):
        send_direct_message(9, "Report is late", confirmation_token=token)
    with pytest.raises(ToolExecutionError, match="different arguments"):
        send_direct_message(10, "Report is ready", confirmation_token=token)
    assert sent(ada) == []


def test_a_token_sends_once(ada):
    token = json.loads(send_direct_message(9, "Report is ready"))["confirmation_token"]
    send_direct_message(9, "Report is ready", confirmation_token=token)

    with pytest.raises(ToolExecutionError, match="already used"):
        send_direct_message(9, "Report is ready", confirmation_token=token)
    assert sent(ada) == ["channel_get", "message_post"]


def test_an_expired_token_sends_nothing(ada, monkeypatch):
    token = json.loads(send_direct_message(9, "Report is ready"))["confirmation_token"]
    later = confirmation.time.time() + confirmation.TTL_SECONDS + 1
    monkeypatch.setattr(confirmation.time, "time", lambda: later)

    with pytest.raises(ToolExecutionError, match="expired"):
        send_direct_message(9, "Report is ready", confirmation_token=token)
    assert sent(ada) == []


@pytest.mark.parametrize("token", ["", "garbage", "1.2", "x.y.z"])
def test_a_missing_or_forged_token_never_sends(ada, token):
    if token:
        with pytest.raises(ToolExecutionError):
            send_direct_message(9, "Report is ready", confirmation_token=token)
    else:
        send_direct_message(9, "Report is ready", confirmation_token=token)
    assert sent(ada) == []


def test_a_token_minted_for_one_hosted_tenant_does_not_send_for_another(ada):
    def as_tenant(subject):
        return tenant.Tenant(subject=subject, base_url="https://x.example",
                             api_key="k", db="db", policy="standard")

    bound = tenant.bind(as_tenant("alice"))
    try:
        token = json.loads(send_direct_message(9, "Hi"))["confirmation_token"]
    finally:
        tenant.reset(bound)

    bound = tenant.bind(as_tenant("mallory"))
    try:
        with pytest.raises(ToolExecutionError, match="different arguments"):
            send_direct_message(9, "Hi", confirmation_token=token)
    finally:
        tenant.reset(bound)
    assert sent(ada) == []


def test_a_channel_preview_names_the_room_and_how_many_it_reaches(team):
    preview = json.loads(send_channel_message(44, "Standup moved"))

    assert preview["preview"]["channel"] == "Team"
    assert preview["preview"]["members_reached"] == 3
    assert sent(team) == []

    token = preview["confirmation_token"]
    assert json.loads(send_channel_message(
        44, "Standup moved", confirmation_token=token))["message_id"] == 92


def test_a_chatter_preview_says_who_would_be_emailed(order_with_customer):
    preview = json.loads(notify_user(
        "sale.order", 42, "Shipped", [7], subtype="comment", force=True))

    assert preview["preview"]["emailed_external_followers"] == ["Customer"]
    assert "EMAILED" in preview["preview"]["route"]
    assert sent(order_with_customer) == []


def test_a_note_preview_emails_no_follower(order_with_customer):
    preview = json.loads(notify_user("sale.order", 42, "Checked", [7]))

    assert preview["preview"]["emailed_external_followers"] == []
    assert sent(order_with_customer) == []


def test_a_refusal_comes_before_any_preview(order_with_customer):
    """An audience that would be emailed is refused outright: there is no
    preview to approve, so no token can push the comment through."""
    with pytest.raises(ToolExecutionError, match="REFUSED"):
        notify_user("sale.order", 42, "Shipped", [7], subtype="comment")
    assert sent(order_with_customer) == []


def test_a_read_only_connection_refuses_the_preview(ada, monkeypatch):
    monkeypatch.setenv("ODOO_MCP_ALLOW", "none")

    with pytest.raises(ToolExecutionError, match="read-only"):
        send_direct_message(9, "Report is ready")
    assert sent(ada) == []
