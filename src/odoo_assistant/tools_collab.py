#!/usr/bin/env python3
"""Collaboration and document tools (PRD §5B lines 359-421).

Four tools — `notify_user`, `create_activity`, `download_docs`, `generate_pdf`
— registered by `register(mcp)`. They wrap `documents.Documents` and
`collaboration.Collab`, the verified scripts, which are never re-implemented
here: this module only decides what may run and reports what happened.

**Why `notify_user` is more than a passthrough.** references/SKILL.md rule 6:
on the reference instance 12 of 12 sampled sales orders had a CUSTOMER among
their followers, so a `mail.mt_comment` posted to "leave a quick note" emails
that customer. Measured on one order, same recipients: `mt_note` -> 1 inbox
notification, 0 emails; `mt_comment` -> 2 notifications, one of them an email
to the customer. So the audience is read BEFORE anything is posted, the
default path is `Documents.tell()` (`message_notify`: reaches exactly the
users named and subscribes nobody), and the comment path is refused while an
external follower exists unless the caller says `force=True`.

That 0-email figure is a property of the RECIPIENT, not of the subtype. Odoo's
own `message_notify` docstring says it "pushes notifications on inbox or by
email depending on the user configuration", and `_notify_thread` calls
`_notify_thread_by_inbox`, `_notify_thread_by_email` and `_by_web_push` in
turn. Verified live on both settings of one user: `notification_type='inbox'`
gave a `mail.notification` of type inbox, status sent, and no `mail.mail`;
`'email'` gave a `mail.mail`. So a note never reaches anyone you did not name
— which is the guarantee worth having — but it can still leave by mail.

**The order every tool here keeps**, and why each step sits where it does:

    decide the plan from the arguments   a bad subtype costs zero Odoo calls
    gate() the method that will run      a refusal costs zero Odoo calls
    read, then refuse on what was read   the external-follower check
    post inside the only try             what failed there may have landed

Deterministic refusals — an unknown subtype, a gate decision, an external
follower — are raised OUTSIDE any `try`. Inside one, `deliver()`'s
`ToolExecutionError` would be caught by `except Exception` and re-mapped to
"UNCERTAIN: may or may not have been applied", turning "nothing was touched"
into "something might have been".

Two facts read from the sources rather than assumed:

  * The method handed to `gate()` is the one that will ACTUALLY run:
    `message_notify` for a note (documents.py:264), `message_post` for a
    comment (documents.py:346). Both are L1 in `safety_layer.WRITE_L1` — the
    level cannot express this danger, because it depends on the subtype and
    not on the method name. The audience check above is the enforcement.
  * The document tools gate on the `ir.attachment` query, NOT on
    `(model, "read")`. `account.move` is a GUARDED_MODEL and `read` is a
    guarded method, so `gate("account.move", "read", [5775])` raises the
    move_type SafetyViolation over an id list that carries no domain to
    filter — it would refuse every invoice PDF for a reason that does not
    apply to it.

The 5-6 parameter signatures are the MCP wire contract from PRD §5B, not a
value object waiting to be extracted.
"""
import sys
import tempfile
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

# Same bootstrap as server.py: the nine scripts are flat modules imported by
# bare name, from the repo and from an installed wheel alike.
sys.path.insert(0, str(Path(__file__).parent / "odoo_scripts"))

from odoo_assistant.server_errors import (  # noqa: E402
    ToolOutcome,
    handle_odoo_exception,
    tool_result,
)
from odoo_assistant.server_safety import gate  # noqa: E402

from collaboration import Collab  # noqa: E402  (needs the bootstrap above)
from documents import Documents  # noqa: E402
from odoo_client import Odoo  # noqa: E402


def _odoo() -> Odoo:
    """The shared client, resolved at call time.

    `server` is imported inside the function on purpose: server.py imports
    THIS module to register the tools, so a top-level import back would be a
    cycle. Reading the attribute at call time is also what lets the suite
    inject a double (tests/conftest.py monkeypatches `server._get_odoo`).
    """
    from odoo_assistant import server

    return server._get_odoo()


def _external_refusal(model: str, record_id: int, external: list[str]) -> str:
    """What the caller is told instead of emailing somebody's customer."""
    return (
        f"REFUSED: subtype='comment' posts a mail.mt_comment on "
        f"{model}/{record_id}, which emails every follower. "
        f"{len(external)} follower(s) are not employees of this instance and "
        f"would receive that email: {', '.join(external)}.\n"
        f"Nothing was posted. Use subtype='note' to reach only the users you "
        f"named — message_notify touches no follower at all — or "
        f"call again with force=True if emailing those people is the intent."
    )


def notify_user(
    model: str,
    record_id: int,
    message: str,
    user_ids: list[int],
    subtype: str = "note",
    force: bool = False,
) -> str:
    """Notify users on a record's chatter. Internal by default.

    Args:
        model: the Odoo model, e.g. "sale.order".
        record_id: id of the record to write on.
        message: the body — plain text or simple HTML.
        user_ids: res.users ids to notify.
        subtype: "note" reaches EXACTLY the users you name and nobody else —
            but each of them through their OWN Odoo notification setting,
            inbox or email, so it is not a promise that no mail leaves;
            "comment" posts to the chatter and EMAILS every follower,
            customers included. A comment is refused while an external
            follower exists, unless force=True.
        force: post the comment anyway, knowing those people get an email.
    """
    match subtype:
        case "note":
            method, emails_followers = "message_notify", False
        case "comment":
            method, emails_followers = "message_post", True
        case _:
            return ToolOutcome(True, (
                f"Unknown subtype {subtype!r}. Nothing was posted. Use "
                f"'note' (only the users you name) or 'comment' (posts to "
                f"the chatter and emails every follower)."
            )).deliver()

    decision = gate(model, method, record_id)
    if not decision.allowed:
        return ToolOutcome(True, decision.reason).deliver()

    try:
        docs = Documents(_odoo())
        audience = docs.audience(model, record_id)  # always, before posting
    except Exception as exc:
        return handle_odoo_exception(exc, phase="before_mutation").deliver()

    if emails_followers and audience["external"] and not force:
        return ToolOutcome(
            True, _external_refusal(model, record_id, audience["external"])
        ).deliver()

    try:
        delivery = (
            docs.notify(model, record_id, message, users=user_ids,
                        subtype="mail.mt_comment")
            if emails_followers
            else docs.tell(model, record_id, message, users=user_ids)
        )
    except Exception as exc:
        return handle_odoo_exception(
            exc, lambda: Collab(_odoo()).history(model, record_id, limit=3),
            phase="after_mutation_possible",
        ).deliver()
    return tool_result(
        {"subtype": subtype, "audience": audience, "delivery": delivery})


def create_activity(
    model: str,
    record_id: int,
    summary: str,
    user_id: int,
    days: int = 0,
    activity_type: str | None = None,
) -> str:
    """Schedule an activity: the only notification that carries a deadline.

    A chatter note is passive. An activity appears in the assignee's To-Do
    list and turns overdue when the date passes.

    Args:
        model: the Odoo model, e.g. "crm.lead".
        record_id: id of the record the activity hangs off.
        summary: the one-line title the assignee will read.
        user_id: res.users id of the assignee.
        days: deadline offset from today, in days.
        activity_type: substring of an activity type name, e.g. "call".
            Activity types differ per instance; the first available type is
            used when this is omitted or matches nothing.
    """
    decision = gate("mail.activity", "create", None, {
        "res_model": model, "res_id": record_id,
        "summary": summary, "user_id": user_id,
    })
    if not decision.allowed:
        return ToolOutcome(True, decision.reason).deliver()
    try:
        activity = Collab(_odoo()).todo(
            model, record_id, summary, user_id, days=days,
            type_name=activity_type,
        )
    except Exception as exc:
        return handle_odoo_exception(
            exc, lambda: Collab(_odoo()).pending(user_id=user_id, model=model),
            phase="after_mutation_possible",
        ).deliver()
    return tool_result(activity)


def download_docs(model: str, record_id: int, dest_dir: str = "") -> str:
    """Save every document of a record to disk — chatter files included.

    Returns {"saved": [paths], "skipped": [[name, why]]}. `skipped` is not
    noise: a database restored without its filestore keeps the attachment
    rows and loses the bytes, and an empty result would read exactly like
    "this record has no attachments".

    Args:
        model: the Odoo model, e.g. "account.move".
        record_id: id of the record whose documents to fetch.
        dest_dir: directory to write the files into. Defaults to this
            platform's temporary directory — "/tmp" does not exist on Windows.
    """
    decision = gate("ir.attachment", "search_read",
                    [["res_model", "=", model], ["res_id", "=", record_id]])
    if not decision.allowed:
        return ToolOutcome(True, decision.reason).deliver()
    try:
        result = Documents(_odoo()).download(
            model, record_id, dest_dir or tempfile.gettempdir())
    except Exception as exc:
        return handle_odoo_exception(exc, phase="before_mutation").deliver()
    return tool_result(result)


def generate_pdf(model: str, record_id: int, dest_dir: str = "") -> str:
    """Render the PDF of a record and return where it was saved.

    An already rendered PDF is reused. Otherwise the model's own print/send
    wizard produces it, and that wizard can also SEND the document — which is
    why this is gated on `action_send_and_print` (L3_STATE_CHANGE) rather
    than as a plain read.

    Args:
        model: the Odoo model, e.g. "account.move".
        record_id: id of the record to print.
        dest_dir: directory to write the PDF into. Defaults to this platform's
            temporary directory — "/tmp" does not exist on Windows.
    """
    decision = gate(model, "action_send_and_print", record_id)
    if not decision.allowed:
        return ToolOutcome(True, decision.reason).deliver()
    try:
        path = Documents(_odoo()).generate_pdf(
            model, record_id, dest_dir or tempfile.gettempdir())
    except Exception as exc:
        # The wizard can SEND the document, and no read can tell whether an
        # email left — so this failure names no state rather than a wrong one.
        return handle_odoo_exception(exc, phase="after_mutation_possible").deliver()
    return tool_result({"path": path})


def register(mcp: MCPServer) -> None:
    """Register the four tools on `mcp`.

    `.tool()` returns the decorated function unchanged (verified on SDK 2.0),
    so the plain functions above stay directly callable — by the consolidated
    server and by the tests alike.
    """
    tools: tuple[Any, ...] = (
        notify_user, create_activity, download_docs, generate_pdf)
    for tool in tools:
        mcp.tool()(tool)
