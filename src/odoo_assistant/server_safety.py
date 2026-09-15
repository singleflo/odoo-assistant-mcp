#!/usr/bin/env python3
"""The gate every tool call passes through before it reaches Odoo.

Until 0.1.x this gate judged a call by CLASSIFYING its effect onto a numbered
scale and comparing the number against a ceiling. The scale answered "what
does this do", but the question an operator actually has is "may it run" — and
people answer that in method names, not ordinals: "never cancel orders", "no
mass mail", "read-only". Nobody could explain in a minute which number allowed
`action_confirm` but refused `unlink`. So the ceiling is gone, and two
operator-owned lists of Odoo method names replace it — deny is a list of names
a human can read in a config file, which is where the decision was always
meant to live. All three variables are read from the process environment at
CALL time, so a host-config edit is the whole story:

  * ODOO_MCP_ALLOW   what may run. Unset (or empty) means `*` — every method
                     not denied. The single value `none` makes the server
                     read-only. Anything else is a comma-separated list of
                     `method` (any model) or `model:method` (one model).
  * ODOO_MCP_DENY    what may not. Unset (or empty) means DEFAULT_DENY below;
                     a value set REPLACES the default entirely — that is how
                     an operator re-enables `action_cancel` (ODOO_MCP_DENY=
                     unlink).
  * ODOO_MCP_ALLOW_UNLINK  the only key that can grant `unlink`, because
                     deletion is the one action that cannot be undone, and a
                     name in a comma-separated list must never be enough to
                     grant it.

Matching is EXACT string equality on the whole entry. No prefix, no substring:
`action_cancel` never matches `button_cancel`, `action_send` never matches
`action_send_and_print` — a human read and approved these exact names, and a
looser match would let lookalikes through that human never saw. Deny is
checked before allow, so an entry on both lists refuses.

Three rules the wrapper keeps from `safety_layer.py`, unchanged:

  * Reads (READ_METHODS) are never subject to the lists — a read has no effect
    for a list to govern. The `account.move` structural guard still applies to
    them, because a meaningless read is its own hazard (3.613 mixed records).
  * The detectors are fed `execute_kw`'s POSITIONAL shape — `[ids]`, `[vals]`
    or `[ids, vals]`. Archive detection reads the dicts inside `args`; hand it
    a dict and it quietly reports a harmless write for a 600-record archive.
  * `write` with `active: False` and `action_archive` both carry the virtual
    name `archive` into the lists, so denying `archive` refuses hiding records
    however they are spelled. `create` gets no such virtual name — creating an
    inactive record hides nothing that existed.

An allowed decision IS the standing consent the scripts ask for: the gate's
`allowed` field is the enforcement decision; nothing is passed downstream
because Writer has no such parameter — gate IS the sole enforcement point.
The lists are where the human granted that consent, once, out of band.
"""
import os
import sys
from pathlib import Path
from typing import Any, NamedTuple

# Same bootstrap as server.py: the nine scripts are flat modules that import
# each other by bare name, from the repo and from an installed wheel alike.
sys.path.insert(0, str(Path(__file__).parent / "odoo_scripts"))

from safety_layer import (  # noqa: E402
    READ_METHODS,
    SafetyViolation,
    _is_archiving,
    check_guards,
)

# The gate's policy follows a bound tenant first, the environment second —
# `tenant.py` owns the per-request binding this module only reads.
from odoo_assistant import tenant  # noqa: E402

# The six names 0.1.x refused at its default ceiling, plus one deliberate
# addition: `mailing.mailing:action_send`. Under the old default-deny it was
# an unclassified method and therefore refused; the wildcard would now allow
# it, and one call there can email an entire customer base. The entry is
# model-qualified because `action_send` on other models (evolution's test
# wizard) is harmless and must keep working.
DEFAULT_DENY = frozenset({
    "unlink", "archive", "action_cancel", "button_cancel",
    "action_reverse", "action_draft", "mailing.mailing:action_send",
})


class GateResult(NamedTuple):
    """`allowed` is the enforcement decision for the call it permits."""

    allowed: bool
    reason: str


def allowed_methods() -> set[str] | str:
    """The ODOO_MCP_ALLOW list: `"*"`, `"none"`, or the set of entries.

    Read at call time — the environment is the operator's config file, and a
    value that changes after import must still take effect. A bound tenant
    owns the policy instead: `read` is the none sentinel, `standard` the
    wildcard, and the environment is not consulted at all.
    """
    bound = tenant.current()
    if bound is not None:
        return "none" if bound.policy == "read" else "*"
    raw = os.environ.get("ODOO_MCP_ALLOW", "")
    if raw == "none":
        return "none"
    if raw in ("", "*"):
        return "*"
    return _entries(raw)


def denied_methods() -> set[str]:
    """The ODOO_MCP_DENY list, DEFAULT_DENY when unset or empty.

    A set value REPLACES the default rather than extending it: the operator
    wrote a whole list, and a refusal they cannot see in their own file would
    be a name nobody read or approved. A bound tenant always gets the
    package defaults — the hosted server carries no operator's list.
    """
    bound = tenant.current()
    if bound is not None:
        return set(DEFAULT_DENY)
    raw = os.environ.get("ODOO_MCP_DENY", "")
    return set(DEFAULT_DENY) if raw == "" else _entries(raw)


def unlink_allowed() -> bool:
    """Whether ODOO_MCP_ALLOW_UNLINK grants deletion. Read at call time.

    A bound tenant never: the switch belongs to a local operator's machine,
    so a process environment the tenant's host happens to carry cannot grant
    deletion on a hosted connection.
    """
    if tenant.current() is not None:
        return False
    return os.environ.get("ODOO_MCP_ALLOW_UNLINK", "").lower() in ("yes", "true", "1")


def refuse_legacy_environment() -> None:
    """Refuse to start while the removed ceiling variable is still set.

    Any value counts — including a stale `0` that used to mean read-only:
    silently ignoring it would turn a server the operator configured
    read-only into a writing one. An empty string counts as unset (the XDG
    convention `paths._from_env` follows). Called once at startup by
    `server.main`; nothing in this module raises at import time.
    """
    if os.environ.get("ODOO_MCP_MAX_LEVEL", "") == "":
        return
    raise RuntimeError(
        "ODOO_MCP_MAX_LEVEL is no longer supported: the safety ceiling is "
        "gone. Set ODOO_MCP_ALLOW, ODOO_MCP_DENY and ODOO_MCP_ALLOW_UNLINK "
        "instead — see README, 'What the agent may do'.")


def _entries(raw: str) -> set[str]:
    """Split a comma-separated list, trimming spaces and dropping empties.

    Odoo names are case-sensitive, so entries are kept exactly as written.
    """
    return {entry.strip() for entry in raw.split(",") if entry.strip()}


def _positional_args(ids: Any, values: Any) -> list[Any]:
    """Rebuild `execute_kw`'s argument list: `[ids]`, `[vals]` or `[ids, vals]`.

    A bare int is wrapped the way `Writer.write`/`Writer.act` wrap it, so the
    classifier counts targets by rule instead of falling back to "1" because it
    could not read the shape.
    """
    args: list[Any] = []
    if ids is not None:
        args.append([ids] if isinstance(ids, int) else ids)
    if values is not None:
        args.append(values)
    return args


def gate(model: str, method: str, ids: Any = None, values: Any = None) -> GateResult:
    """Decide whether THIS server may call `model.method`, and say why.

    `ids` carries the record ids for a write or an action, and the domain for
    a read — both live in the same first `execute_kw` slot, which is what the
    structural guards read. The reason strings are written for an agent that
    has to stop and explain: each names the call, the entry involved and the
    variable that would change the answer.
    """
    args = _positional_args(ids, values)

    if method.startswith("_"):
        return GateResult(False, (
            f"{model}.{method}: private method. Odoo rejects every method "
            "starting with '_' (check_method_name), so no list can allow it. "
            "Use the public wizard instead."))

    if method in READ_METHODS:
        try:
            check_guards(model, method, args, {})
        except SafetyViolation as violation:
            return GateResult(False, str(violation))
        return GateResult(True, (
            f"{model}.{method} is a read — reads are never subject to the "
            "allow/deny lists."))

    names = {method, f"{model}:{method}"}
    if method == "action_archive" or (
            method == "write" and _is_archiving(method, args, {})):
        names |= {"archive", f"{model}:archive"}

    if method == "unlink":
        if unlink_allowed():
            return GateResult(True, (
                f"{model}.unlink is allowed by ODOO_MCP_ALLOW_UNLINK."))
        if tenant.current() is not None:
            return GateResult(False, (
                f"{model}.unlink: Deletion is never available on the hosted "
                "server; use a local install with ODOO_MCP_ALLOW_UNLINK=yes."))
        return GateResult(False, (
            f"{model}.unlink: deletion is the one action that cannot be "
            "undone. It is granted only by ODOO_MCP_ALLOW_UNLINK=yes; the "
            "ODOO_MCP_ALLOW and ODOO_MCP_DENY lists can never grant it."))

    denied = names & denied_methods()
    if denied:
        return GateResult(False, (
            f"{model}.{method}: refused by ODOO_MCP_DENY, entry "
            f"'{sorted(denied)[0]}'. Remove that entry from the variable to "
            "allow the call — a value set on ODOO_MCP_DENY replaces the "
            "default list entirely."))

    allowed = allowed_methods()
    if not isinstance(allowed, set):
        if allowed == "none":
            if tenant.current() is not None:
                return GateResult(False, (
                    f"{model}.{method}: this connection was authorised as "
                    "read-only; reconnect and choose the standard policy to "
                    "allow it."))
            return GateResult(False, (
                f"{model}.{method}: this is a read-only server "
                "(ODOO_MCP_ALLOW=none). Remove the variable, or set "
                "ODOO_MCP_ALLOW to the entries you want."))
        return GateResult(True, (
            f"{model}.{method} is allowed: ODOO_MCP_ALLOW=* and no "
            "ODOO_MCP_DENY entry matches."))   # the other sentinel, "*"
    permitted = names & allowed
    if permitted:
        return GateResult(True, (
            f"{model}.{method} is allowed by ODOO_MCP_ALLOW entry "
            f"'{sorted(permitted)[0]}'."))
    return GateResult(False, (
        f"{model}.{method}: not in ODOO_MCP_ALLOW. Add '{method}' (any "
        f"model) or '{model}:{method}' (this model only) to the variable."))
