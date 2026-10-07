"""Preview, then confirm: the user approves a message before it leaves.

`send_direct_message`, `send_channel_message` and `notify_user` speak FOR the
user — to a colleague, to a whole channel, to a record's followers, and on the
comment path by email to a customer. Each is therefore split in two calls:

  1. Without a token the tool validates everything it would check before
     sending, sends NOTHING, and answers with a preview — who receives what,
     by which route — plus a `confirmation_token`.
  2. The same call again with that token sends. The token is bound to the
     exact arguments of the preview, so the model cannot show the user one
     message and send another, and it is single-use and expires.

The token is an HMAC under a secret drawn once per process: nothing is stored
per preview, a restart simply invalidates pending previews (the model asks
for a fresh one), and a token minted for one hosted tenant does not verify
for another, because the tenant's subject is part of what is signed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time

from odoo_assistant import tenant

TTL_SECONDS = 600

_SECRET = secrets.token_bytes(32)
_used: dict[str, float] = {}
_used_lock = threading.Lock()


def _signature(tool: str, arguments: dict, expires: int, nonce: str) -> str:
    bound = tenant.current()
    message = json.dumps(
        [bound.subject if bound else "", tool, arguments, expires, nonce],
        sort_keys=True, separators=(",", ":"), default=str)
    return hmac.new(_SECRET, message.encode(), hashlib.sha256).hexdigest()


def issue(tool: str, arguments: dict) -> str:
    """A token that approves exactly `arguments` for `tool`, for TTL_SECONDS.

    The nonce makes every preview's token distinct, so two identical previews
    are two separate approvals rather than one token spent twice."""
    expires = int(time.time()) + TTL_SECONDS
    nonce = secrets.token_hex(8)
    return f"{expires}.{nonce}.{_signature(tool, arguments, expires, nonce)}"


def redeem(token: str, tool: str, arguments: dict) -> str | None:
    """Spend `token` on this call. None when it approves it, else the reason."""
    parts = token.split(".")
    if len(parts) != 3 or not parts[0].isdigit() or not all(parts):
        return "the confirmation_token is malformed"
    expires_text, nonce, signature = parts
    expires = int(expires_text)
    now = time.time()
    if expires < now:
        return "the confirmation_token has expired"
    if not hmac.compare_digest(
            signature, _signature(tool, arguments, expires, nonce)):
        return ("the confirmation_token was issued for different arguments — "
                "the message, recipient or options changed since the preview")
    with _used_lock:
        for spent in [t for t, until in _used.items() if until < now]:
            del _used[spent]
        if token in _used:
            return "the confirmation_token was already used"
        _used[token] = expires
    return None


def preview(tool: str, arguments: dict, details: dict) -> dict:
    """The answer of a call without a token: what WOULD happen, and how to
    approve it."""
    return {
        "status": "NOT SENT — waiting for the user's approval",
        "preview": details,
        "confirmation_token": issue(tool, arguments),
        "next_step": (
            "Show the user this preview — recipients, route and the full "
            "message — and ask whether to send it. Only after they explicitly "
            f"approve, call {tool} again with exactly the same arguments plus "
            "this confirmation_token. If they want changes, call without a "
            "token to get a new preview. Never send without their approval."),
        "expires_in_seconds": TTL_SECONDS,
    }


def refusal(reason: str, tool: str) -> str:
    """The text of a send refused for a bad token."""
    return (f"Nothing was sent: {reason}. Call {tool} without a "
            "confirmation_token to get a fresh preview, show it to the user, "
            "and send only after they approve.")
