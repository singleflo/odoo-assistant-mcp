#!/usr/bin/env python3
"""Safety layer — every write goes through here. Stdlib only.

Two independent protections:

  1. LEVELS L0-L5   what an operation DOES, not what it is called.
                    Default deny: unknown method -> refuse.
  2. GUARDS         model-specific rules that block meaningless queries
                    (account.move without move_type -> 3.613 mixed records).

Classification is by EFFECT. write({'active': False}) archives a record —
same user-visible outcome as deleting it — so it is L4, not L1, even though
the method is `write`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from odoo_client import connect_cli as connect, OdooError  # noqa: E402


class SafetyViolation(Exception):
    """A rule was violated. Not to be swallowed: the agent must stop and
    explain, not retry with a workaround."""


READ_METHODS = {
    "search", "search_read", "search_count", "read", "read_group",
    "web_search_read", "web_read_group", "fields_get", "default_get",
    "get_views", "name_search", "name_get", "read_progress_bar",
}

WRITE_L1 = {
    "create", "write", "copy",
    # Collaboration is L1 by design. These write to the chatter, activities
    # and calendar — they never touch business state, and blocking them would
    # block the audit trail the skill requires after every L3+ action.
    #
    # `message_notify` is the safe channel (named recipients only, no
    # followers). `message_post` with mt_comment CAN email customers, which is
    # why the danger is documented in SKILL.md rule 6 rather than enforced
    # here: the level depends on the subtype, not on the method name.
    "message_post", "message_subscribe", "message_notify",
    "message_unsubscribe",
    # Discuss: `channel_get` finds the 1-to-1 chat with a partner, creating it
    # only when none exists — at most one discuss.channel, no business state.
    # Odoo's own method is used rather than a hand-rolled search+create so the
    # exact-match SQL and the `_broadcast()` that pushes the channel header
    # stay Odoo's responsibility.
    "channel_get",
    # Activities: creating, completing and rescheduling work items.
    "action_feedback", "action_feedback_schedule_next", "action_snooze",
    "action_close_dialog", "activity_schedule",
    # Calendar RSVPs — answering an invitation changes nothing but the answer.
    "do_accept", "do_decline", "do_tentative",
}

# Sending mail actually leaves the building. Not destructive, but not
# reversible either: you cannot unsend. Treated as a state change so it
# requires confirmation.
WRITE_L3_COMMS = {
    "send", "send_mail", "action_sendmail", "action_send_mail",
    "action_send_and_print",
}

WRITE_L3 = {
    "action_confirm", "action_post", "action_validate", "action_done",
    "action_set_won", "action_set_lost", "convert_opportunity",
    "create_invoices", "action_invoice_sent", "action_quotation_send",
    "action_register_payment", "action_approve",
} | WRITE_L3_COMMS

WRITE_L4 = {
    "unlink", "action_cancel", "action_archive", "button_cancel",
    "action_reverse", "action_draft",
}

# Models where an unfiltered query returns a meaningless mixture.
GUARDED_MODELS = {
    "account.move": "move_type",
    "account.move.line": "move_id.move_type",
}

GUARDED_READ_METHODS = {
    "search", "search_read", "search_count", "read", "read_group",
    "web_search_read", "web_read_group", "name_search", "export_data",
}

BATCH_THRESHOLD = 5


def _extract_domain(args, kwargs):
    if kwargs.get("domain") is not None:
        return kwargs["domain"]
    if args and isinstance(args[0], (list, tuple)):
        return args[0]
    return []


def _count_targets(args, kwargs):
    # args may be a dict (payload style) rather than a list — indexing it with
    # [0] raises KeyError and takes the whole classification down with it.
    if isinstance(kwargs, dict) and kwargs.get("ids"):
        return len(kwargs["ids"])
    if isinstance(args, (list, tuple)) and args:
        first = args[0]
        if isinstance(first, (list, tuple)) and first:
            if all(isinstance(x, int) for x in first):
                return len(first)
    return 1


def _is_archiving(method, args, kwargs):
    if method != "write":
        return False
    for a in list(args) + [kwargs.get("vals")]:
        if isinstance(a, dict) and a.get("active") is False:
            return True
    return False


def classify(model, method, args=None, kwargs=None):
    args, kwargs = args or [], kwargs or {}
    if method.startswith("_"):
        return "L5_PRIVATE"
    if method in READ_METHODS:
        return "L0_READ"
    if method in WRITE_L1:
        if _is_archiving(method, args, kwargs):
            return "L4_DESTRUCTIVE"          # archiving hides the record
        if _count_targets(args, kwargs) > BATCH_THRESHOLD:
            return "L2_BATCH"
        return "L1_WRITE"
    if method in WRITE_L3:
        return "L3_STATE_CHANGE"
    if method in WRITE_L4:
        return "L4_DESTRUCTIVE"
    return "L5_UNKNOWN"                       # default deny


def check_guards(model, method, args, kwargs):
    field = GUARDED_MODELS.get(model)
    if not field or method not in GUARDED_READ_METHODS:
        return
    domain = _extract_domain(args, kwargs)

    def has_field(d):
        """Walk the domain STRUCTURE. A substring check would be fooled by
        [('invoice_origin','ilike','move_type')] — a real false negative
        found in review."""
        if not isinstance(d, (list, tuple)):
            return False
        for item in d:
            if isinstance(item, str):
                continue                       # '&', '|', '!'
            if isinstance(item, (list, tuple)) and item:
                f = item[0]
                if isinstance(f, str):
                    if f == field or f.endswith("." + field.split(".")[-1]):
                        return True
                if has_field(item):
                    return True
        return False

    if not has_field(domain):
        raise SafetyViolation(
            f"{model} query without an explicit '{field}' filter.\n"
            f"Domain received: {domain!r}\n\n"
            "This model mixes customer invoices, vendor bills, credit notes "
            "AND raw journal entries. Counting them together produces a "
            "number that matches nothing the user sees on screen.\n"
            "Add the filter, e.g. [['move_type','=','out_invoice']]."
        )


def safe_call(odoo, model, method, args=None, kwargs=None, confirmed=False):
    """The only sanctioned way to reach Odoo."""
    args, kwargs = args or [], kwargs or {}
    level = classify(model, method, args, kwargs)

    if level == "L5_PRIVATE":
        raise SafetyViolation(
            f"{model}.{method}: private method, always rejected by Odoo. "
            "Use the public wizard instead.")
    if level == "L5_UNKNOWN":
        raise SafetyViolation(
            f"{model}.{method}: not in the L0-L4 whitelist (default deny).\n"
            "If legitimate, add it to WRITE_L1/L3/L4 in safety_layer.py — "
            "in the CODE, never approved ad-hoc at runtime.")

    check_guards(model, method, args, kwargs)

    if level in ("L2_BATCH", "L3_STATE_CHANGE", "L4_DESTRUCTIVE") and not confirmed:
        n = _count_targets(args, kwargs)
        extra = f" on {n} records (threshold {BATCH_THRESHOLD})" if level == "L2_BATCH" else ""
        raise SafetyViolation(
            f"{model}.{method} is {level}{extra}: requires explicit "
            "confirmation. Tell the user what will change and wait for a "
            "yes before passing confirmed=True.")

    return odoo.call(model, method, args, kwargs)


if __name__ == "__main__":
    odoo = connect()
    print("Safety layer self-test\n")
    cases = [
        ("blocked: account.move without move_type", "account.move", "search_count",
         [[["state", "=", "posted"]]], {}, False),
        ("allowed: with move_type", "account.move", "search_count",
         [[["move_type", "=", "out_invoice"]]], {}, False),
        ("blocked: account.move.line unfiltered", "account.move.line", "search_count",
         [[]], {}, False),
        ("blocked: substring bypass attempt", "account.move", "search_count",
         [[["invoice_origin", "ilike", "move_type"]]], {}, False),
        ("blocked: unlink without confirmation", "res.partner", "unlink",
         [[999999]], {}, False),
    ]
    for label, model, method, args, kwargs, conf in cases:
        try:
            r = safe_call(odoo, model, method, args, kwargs, confirmed=conf)
            print(f"  PASSED   {label} -> {r}")
        except SafetyViolation as e:
            print(f"  BLOCKED  {label}\n           {str(e).splitlines()[0]}")
        except OdooError as e:
            print(f"  ODOO-ERR {label} -> {str(e)[:70]}")
