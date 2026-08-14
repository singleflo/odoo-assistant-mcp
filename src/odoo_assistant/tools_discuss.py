"""Discuss tools: talking TO PEOPLE, as opposed to annotating a document.

Odoo has two notification mechanisms that are constantly confused, and the
difference decides whether a human ever sees the message.

`message_notify` — what `notify_user` in tools_collab.py does — writes a
notification about a RECORD. It lands in the Inbox bell, and only for a
recipient whose `notification_type` is 'inbox'; set to 'email' the same call
tries to send mail and the systray stays empty. Verified live on one user
across both settings.

A Discuss message is a CONVERSATION. `channel_get` opens the 1-to-1 chat and
`message_post` puts a message in it, which the bus pushes to every member in
real time: the chat window pops up and the counter moves. It reaches the
recipient whatever their notification preference says, because it never goes
near mail. Verified live: `mail.mail` was unchanged, 109 before and after,
against a recipient set to 'email'.

Two properties here are structural rather than promised:

  * no `partner_ids` is ever passed, so no message carries a mention. In a
    Discuss channel `_notify_get_recipients` builds inbox/email notifications
    ONLY for mentioned partners, so omitting them is what keeps mail at zero.
  * `message_type='comment'` is always passed. `discuss.channel.message_post`
    defaults to 'notification', and `_notify_get_recipients` discards anything
    that is not comment/email/whatsapp — with the default the message appears
    in the channel and notifies nobody at all.

`list_message_targets()` exists because an agent that cannot see the roster
guesses at ids. Ask it first: it answers who exists, who is online, and which
conversations are already open.
"""
import sys
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

# Same bootstrap as server.py: the nine scripts are flat modules imported by
# bare name, from the repo and from an installed wheel alike.
sys.path.insert(0, str(Path(__file__).parent / "odoo_scripts"))

from odoo_assistant.server_errors import (  # noqa: E402
    ToolExecutionError,
    ToolOutcome,
    handle_odoo_exception,
    tool_result,
)
from odoo_assistant.server_safety import gate  # noqa: E402

from odoo_client import Odoo  # noqa: E402

CHANNEL_MODEL = "discuss.channel"
MEMBER_MODEL = "discuss.channel.member"
USER_LIMIT = 100
MESSAGE_LIMIT = 30


def _odoo() -> Odoo:
    """The shared client, resolved at call time to avoid an import cycle and
    to let the suite inject a double."""
    from odoo_assistant import server

    return server._get_odoo()


def _rows(payload: object) -> list[dict]:
    """Whatever crossed the wire, as a list of dicts. XML-RPC is untyped."""
    return [row for row in payload if isinstance(row, dict)] if isinstance(
        payload, list) else []


def _gate_or_raise(model: str, method: str, target: object) -> None:
    decision = gate(model, method, target)
    if not decision.allowed:
        raise ToolExecutionError(decision.reason)


def _my_partner_id(odoo: Odoo) -> int:
    """The partner behind the API key — the author of everything sent here."""
    rows = _rows(odoo.search_read("res.users", [["id", "=", odoo.uid]],
                                  ["partner_id"]))
    partner = rows[0]["partner_id"] if rows else None
    if not isinstance(partner, list):
        raise ToolExecutionError(
            f"Could not resolve the partner of uid {odoo.uid}. Nothing was "
            f"sent. This key's user has no partner, which Discuss requires."
        )
    return int(partner[0])


def _outsiders(odoo: Odoo, channel_id: int) -> list[str]:
    """Members who are NOT employees of this instance.

    A guest has no partner at all and counts as an outsider by construction.
    For the rest the test is the one `Documents.audience` applies: staff is a
    `res.users` with `share=False`. OdooBot is the exception — it is a system
    user with `share=True`, a member of every internal channel, and refusing a
    channel because the bot is in it would refuse them all. It is excluded by
    its stable xml_id `base.partner_root`, not by name.
    """
    members = _rows(odoo.search_read(
        MEMBER_MODEL, [["channel_id", "=", channel_id]],
        ["partner_id", "guest_id"], limit=200))

    named: dict[int, str] = {}
    outside: list[str] = []
    for member in members:
        guest = member.get("guest_id")
        if isinstance(guest, list):
            outside.append(f"{guest[1]} (guest)")
            continue
        partner = member.get("partner_id")
        if isinstance(partner, list):
            named[int(partner[0])] = str(partner[1])

    if named:
        staff = _rows(odoo.search_read(
            "res.users", [["partner_id", "in", list(named)],
                          ["share", "=", False]], ["partner_id"], limit=200))
        employees = {row["partner_id"][0] for row in staff
                     if isinstance(row.get("partner_id"), list)}
        employees |= _bot_partner_ids(odoo, named)
        outside += [name for pid, name in named.items() if pid not in employees]
    return outside


def _bot_partner_ids(odoo: Odoo, candidates: dict[int, str]) -> set[int]:
    """OdooBot's partner id, when it is among the members — never an outsider."""
    bot = _rows(odoo.search_read(
        "ir.model.data",
        [["module", "=", "base"], ["name", "=", "partner_root"],
         ["model", "=", "res.partner"]], ["res_id"]))
    ids = {row["res_id"] for row in bot if isinstance(row.get("res_id"), int)}
    return ids & set(candidates)


def list_message_targets() -> str:
    """Who can be messaged and where — ASK THIS BEFORE SENDING ANYTHING.

    Two lists in one call, because an agent that cannot see the roster invents
    ids:

      * `users`: the internal, active users, each with `im_status` — 'online',
        'away' (idle 30 minutes) or 'offline'. Presence is worth reading first:
        a Discuss message is delivered either way, but "offline" tells you
        nobody is going to answer right now.
      * `conversations`: the ones the sender already belongs to and has not
        archived, with `channel_type` — 'chat' is a 1-to-1, 'group' is a
        private multi-party, 'channel' is a room that may hold the whole
        company. `members` and `unread` are there so a broadcast is a
        deliberate choice rather than a surprise.

    Use `send_direct_message` for a person and `send_channel_message` for a
    conversation in this list. Neither of them is the tool for annotating an
    invoice or an order — that is `notify_user`.
    """
    try:
        odoo = _odoo()
        _gate_or_raise("res.users", "search_read", [])

        users = _rows(odoo.search_read(
            "res.users", [["share", "=", False], ["active", "=", True]],
            ["name", "login", "im_status"], limit=USER_LIMIT))

        mine = _rows(odoo.search_read(
            MEMBER_MODEL,
            [["partner_id", "=", _my_partner_id(odoo)], ["is_pinned", "=", True]],
            ["channel_id", "message_unread_counter"], limit=USER_LIMIT))
        unread = {row["channel_id"][0]: row.get("message_unread_counter", 0)
                  for row in mine if isinstance(row.get("channel_id"), list)}

        channels = _rows(odoo.search_read(
            CHANNEL_MODEL, [["id", "in", list(unread)]],
            ["name", "channel_type", "member_count"], limit=USER_LIMIT))

        return tool_result({
            "users": [{"user_id": u["id"], "name": u["name"],
                       "login": u["login"], "presence": u.get("im_status")}
                      for u in users],
            "conversations": [{"channel_id": c["id"], "name": c["name"],
                               "type": c["channel_type"],
                               "members": c.get("member_count"),
                               "unread": unread.get(c["id"], 0)}
                              for c in channels],
        })
    except Exception as exc:  # noqa: BLE001 — mapped to a tool-shaped answer
        return handle_odoo_exception(exc, phase="before_mutation").deliver()


def read_conversation(channel_id: int, limit: int = MESSAGE_LIMIT) -> str:
    """Read what was said in a Discuss conversation, newest first.

    This is how you answer "what did they write to me" or "what is going on in
    that channel". `list_message_targets` gives you the `channel_id` and says
    how many messages are unread.

    Reading does not mark anything as read: the unread counter belongs to the
    member record and only the user's own client clears it.

    Args:
        channel_id: the Discuss channel, from `list_message_targets`.
        limit: how many recent messages to return.
    """
    try:
        odoo = _odoo()
        _gate_or_raise("mail.message", "search_read", [])
        rows = _rows(odoo.search_read(
            "mail.message",
            [["model", "=", CHANNEL_MODEL], ["res_id", "=", channel_id]],
            ["date", "author_id", "body", "message_type"],
            limit=limit, order="date desc"))
        return tool_result([{
            "date": row.get("date"),
            "author": row["author_id"][1] if isinstance(
                row.get("author_id"), list) else "system",
            "body": row.get("body"),
        } for row in rows])
    except Exception as exc:  # noqa: BLE001
        return handle_odoo_exception(exc, phase="before_mutation").deliver()


def _post(odoo: Odoo, channel_id: int, message: str) -> object:
    """The one place a Discuss message is written. See the module docstring
    for why `message_type` is spelled out and `partner_ids` is absent."""
    _gate_or_raise(CHANNEL_MODEL, "message_post", [channel_id])
    return odoo.call(CHANNEL_MODEL, "message_post", [[channel_id]], {
        "body": message,
        "message_type": "comment",
        "subtype_xmlid": "mail.mt_comment",
    })


def send_direct_message(user_id: int, message: str) -> str:
    """Send a 1-to-1 Discuss message that appears in the user's chat systray.

    This is the tool for "tell X", "message X", "warn X". It opens the private
    chat with that user — reusing the existing one, `channel_get` matches on
    the exact pair — and posts there. The bus pushes it in real time and it
    persists, so a recipient who is offline finds it on their next login.

    It reaches them whatever their notification setting says, and sends no
    email at all. That is the difference from `notify_user`, which follows the
    recipient's preference and lands in the Inbox bell instead.

    Args:
        user_id: res.users id of the recipient — from `list_message_targets`.
        message: the body, plain text or simple HTML.
    """
    odoo = _odoo()
    rows = _rows(odoo.search_read("res.users", [["id", "=", user_id]],
                                  ["name", "partner_id", "share"]))
    if not rows:
        return ToolOutcome(True, (
            f"No res.users with id {user_id}. Nothing was sent. Call "
            f"list_message_targets to see who exists."
        )).deliver()

    recipient = rows[0]
    partner = recipient.get("partner_id")
    if not isinstance(partner, list):
        return ToolOutcome(True, (
            f"User {recipient['name']} has no partner, which Discuss "
            f"needs to open a chat. Nothing was sent."
        )).deliver()

    try:
        _gate_or_raise(CHANNEL_MODEL, "channel_get", [[partner[0]]])
        opened = odoo.call(CHANNEL_MODEL, "channel_get", [[partner[0]]])
        channels = opened.get(CHANNEL_MODEL) if isinstance(opened, dict) else None
        if not channels:
            return ToolOutcome(True, (
                f"channel_get returned no channel for {recipient['name']}. "
                f"Nothing was sent."
            )).deliver()

        channel_id = int(channels[0]["id"])
        message_id = _post(odoo, channel_id, message)
        return tool_result({
            "sent_to": recipient["name"],
            "channel_id": channel_id,
            "message_id": message_id,
            "delivery": "Discuss chat — real time, no email, persists offline",
        })
    except Exception as exc:  # noqa: BLE001
        return handle_odoo_exception(exc).deliver()


def send_channel_message(channel_id: int, message: str) -> str:
    """Post to an EXISTING Discuss channel — everyone in it sees this.

    The channel is never created here: `list_message_targets` shows the ones
    that exist, and posting to a room of the wrong size is not recoverable by
    deleting the message afterwards.

    Members who are not employees of this instance — portal users, guests —
    are named in a refusal rather than written to, the same rule `notify_user`
    applies to external followers. Nothing is posted in that case.

    Args:
        channel_id: from `list_message_targets`.
        message: the body, plain text or simple HTML.
    """
    odoo = _odoo()
    rows = _rows(odoo.search_read(CHANNEL_MODEL, [["id", "=", channel_id]],
                                  ["name", "channel_type", "member_count"]))
    if not rows:
        return ToolOutcome(True, (
            f"No discuss.channel with id {channel_id}. Nothing was posted. "
            f"Call list_message_targets to see the conversations that exist."
        )).deliver()

    channel = rows[0]
    outside = _outsiders(odoo, channel_id)
    if outside:
        return ToolOutcome(True, (
            f"REFUSED: {channel['name']} ({channel['channel_type']}, "
            f"{channel.get('member_count')} members) includes "
            f"{len(outside)} member(s) who are not employees of this "
            f"instance: {', '.join(outside)}.\nNothing was posted. Use "
            f"send_direct_message to reach a colleague privately."
        )).deliver()

    try:
        message_id = _post(odoo, channel_id, message)
        return tool_result({
            "posted_to": channel["name"],
            "channel_type": channel["channel_type"],
            "members_reached": channel.get("member_count"),
            "message_id": message_id,
        })
    except Exception as exc:  # noqa: BLE001
        return handle_odoo_exception(exc).deliver()


def register(mcp: MCPServer) -> None:
    """Register the four Discuss tools on `mcp`."""
    tools: tuple[Any, ...] = (
        list_message_targets, read_conversation,
        send_direct_message, send_channel_message)
    for tool in tools:
        mcp.tool()(tool)
