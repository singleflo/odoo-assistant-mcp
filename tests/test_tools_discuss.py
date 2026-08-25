"""Discuss tools across the Odoo 17 model rename."""
import json

import pytest

from odoo_assistant import tools_discuss


def test_odoo_17_discuss_model_is_resolved_once_per_connection(mock_odoo):
    """Given the renamed model exists, When two conversations are read, Then
    both use it after a single model probe."""
    mock_odoo.set_results("ir.model", 1, method="search_count")
    mock_odoo.set_results("mail.message", [])

    tools_discuss.read_conversation(7)
    tools_discuss.read_conversation(8)

    probes = [call for call in mock_odoo.calls
              if call["model"] == "ir.model"]
    reads = [call for call in mock_odoo.calls
             if call["model"] == "mail.message"]
    assert len(probes) == 1
    assert probes[0]["domain"] == [["model", "=", "discuss.channel"]]
    assert [call["domain"][0] for call in reads] == [
        ["model", "=", "discuss.channel"],
        ["model", "=", "discuss.channel"],
    ]


def test_odoo_16_mail_model_is_used_when_discuss_model_is_absent(mock_odoo):
    """Given the renamed model is absent, When a conversation is read, Then
    its messages are selected with the Odoo 16 model name."""
    mock_odoo.set_results("ir.model", 0, method="search_count")
    mock_odoo.set_results("mail.message", [])

    tools_discuss.read_conversation(7)

    assert mock_odoo.last_call["domain"][0] == ["model", "=", "mail.channel"]


def test_odoo_16_list_targets_uses_mail_channel_and_member(mock_odoo):
    """Given Odoo 16 Discuss models, When targets are listed, Then both channel
    and membership reads use the legacy names."""
    mock_odoo.set_results("ir.model", 0, method="search_count")
    mock_odoo.set_results("res.users", [
        {
            "id": 2, "name": "Owner", "login": "owner",
            "im_status": "online", "partner_id": [5, "Owner"],
        },
    ])
    mock_odoo.set_results("mail.channel.member", [
        {"channel_id": [44, "Team"], "message_unread_counter": 2},
    ])
    mock_odoo.set_results("mail.channel", [
        {"id": 44, "name": "Team", "channel_type": "group", "member_count": 3},
    ])

    result = json.loads(tools_discuss.list_message_targets())

    discuss_reads = [call["model"] for call in mock_odoo.calls
                     if call["model"] in {"mail.channel", "mail.channel.member"}]
    assert discuss_reads == ["mail.channel.member", "mail.channel"]
    assert result["conversations"][0]["channel_id"] == 44


@pytest.mark.parametrize("renamed, channel_model", [
    (0, "mail.channel"), (1, "discuss.channel"),
])
def test_channel_get_result_is_read_under_the_resolved_model_key(
    mock_odoo, monkeypatch, renamed, channel_model
):
    """Given `channel_get` answers a dict KEYED BY THE MODEL NAME, When a direct
    message is sent, Then the key read is this instance's own — the lookup that
    silently yields nothing if the key and the call disagree."""
    monkeypatch.delenv("ODOO_MCP_MAX_LEVEL", raising=False)
    mock_odoo.set_results("ir.model", renamed, method="search_count")
    mock_odoo.set_results("res.users", [
        {"id": 9, "name": "Ada", "partner_id": [12, "Ada"], "share": False},
    ])
    mock_odoo.set_results(
        channel_model, {channel_model: [{"id": 44}]}, method="channel_get"
    )
    mock_odoo.set_results(channel_model, 91, method="message_post")

    result = json.loads(tools_discuss.send_direct_message(9, "Hello"))

    channel_calls = [call for call in mock_odoo.calls
                     if call["model"] == channel_model]
    assert result["channel_id"] == 44
    assert [call["method"] for call in channel_calls] == [
        "channel_get", "message_post",
    ]


def test_odoo_16_channel_message_uses_mail_channel_members(mock_odoo, monkeypatch):
    """Given an internal Odoo 16 channel, When a message is posted, Then the
    audience check and write use the two legacy Discuss models."""
    monkeypatch.delenv("ODOO_MCP_MAX_LEVEL", raising=False)
    mock_odoo.set_results("ir.model", 0, method="search_count")
    mock_odoo.set_results("mail.channel", [
        {"id": 44, "name": "Team", "channel_type": "group", "member_count": 1},
    ])
    mock_odoo.set_results("mail.channel.member", [
        {"partner_id": [12, "Ada"], "guest_id": False},
    ])
    mock_odoo.set_results("res.users", [
        {"partner_id": [12, "Ada"]},
    ])
    mock_odoo.set_results("ir.model.data", [])
    mock_odoo.set_results("mail.channel", 92, method="message_post")

    result = json.loads(tools_discuss.send_channel_message(44, "Hello team"))

    assert result["message_id"] == 92
    assert any(call["model"] == "mail.channel.member" for call in mock_odoo.calls)
    assert mock_odoo.last_call["model"] == "mail.channel"
    assert mock_odoo.last_call["method"] == "message_post"
