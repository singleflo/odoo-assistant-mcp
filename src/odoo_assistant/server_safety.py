#!/usr/bin/env python3
"""The gate every tool call passes through before it reaches Odoo.

`safety_layer.classify()` answers what an operation DOES (L0-L5). This module
answers whether THIS server may do it, by comparing that answer against a
ceiling read from the environment. Nothing here decides a level: no tool is
ever tagged "L3" in code, because the level of `run_action` depends on the
method string the caller passed, and only the classifier knows it.

Three rules the wrapper exists to keep:

  * The classifier is fed `execute_kw`'s POSITIONAL shape — `[ids]`, `[vals]`
    or `[ids, vals]`. Batch detection and archive detection both read `args[0]`
    and `args[1]`; hand them a dict and they quietly report a harmless L1 for a
    600-record archive. (`collaboration.py::_guard` does exactly that. Not our
    bug to fix there — the scripts are verified sources — but ours never to
    inherit, which `tests/test_safety.py` pins.)
  * `classify()` does NOT call `check_guards()`; `safe_call()` calls both. A
    wrapper that only classified would let an unfiltered `account.move` query
    through the front door the guard was built to close.
  * Both L5 variants are refused whatever the ceiling says. `safe_call()`
    rejects them unconditionally, so a gate that allowed them would promise
    something the enforcement point below it refuses.

An allowed decision IS the standing consent the scripts ask for: the caller
passes `confirmed=True` to `safe_call`/`Writer` on the strength of it. The
ceiling is where the human granted that consent, once, out of band.
"""
import os
import sys
from pathlib import Path
from typing import Any, NamedTuple

# Same bootstrap as server.py: the nine scripts are flat modules that import
# each other by bare name, from the repo and from an installed wheel alike.
sys.path.insert(0, str(Path(__file__).parent / "odoo_scripts"))

from safety_layer import SafetyViolation, check_guards, classify  # noqa: E402

LEVEL_ORDINALS = {
    "L0_READ": 0,
    "L1_WRITE": 1,
    "L2_BATCH": 2,
    "L3_STATE_CHANGE": 3,
    "L4_DESTRUCTIVE": 4,
    "L5_UNKNOWN": 5,
    "L5_PRIVATE": 5,
}

# L3 lets the agent confirm orders and post invoices — the work it is here for.
# L4 (unlink, cancel, archive) and L5 stay behind an explicit opt-in.
DEFAULT_MAX_LEVEL = 3


class GateResult(NamedTuple):
    """`allowed` doubles as the `confirmed=` argument for the call it permits."""

    allowed: bool
    level: str
    reason: str


def max_level() -> int:
    """The highest level this server may execute, from ODOO_MCP_MAX_LEVEL.

    Garbage falls back to the default rather than raising: a typo in a host's
    config file must not turn every tool call into a stack trace.
    """
    try:
        return int(os.environ.get("ODOO_MCP_MAX_LEVEL", ""))
    except ValueError:
        return DEFAULT_MAX_LEVEL


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
    """Classify `model.method` for real, then judge it against the ceiling.

    `ids` carries the record ids for a write or an action, and the domain for a
    read — both live in the same first `execute_kw` slot, which is what the
    structural guards read.
    """
    args = _positional_args(ids, values)
    level = classify(model, method, args, {})

    if level == "L5_PRIVATE":
        return GateResult(False, level, (
            f"{model}.{method}: private method. Odoo rejects every method "
            "starting with '_' (check_method_name), so no ceiling can allow "
            "it. Use the public wizard instead."))
    if level == "L5_UNKNOWN":
        return GateResult(False, level, (
            f"{model}.{method}: not in the L0-L4 whitelist, so its effect is "
            "unknown and it is refused by default deny. If it is legitimate, "
            "add it to WRITE_L1/L3/L4 in safety_layer.py — in the code, "
            "reviewed, never approved ad-hoc at runtime."))
    if level not in LEVEL_ORDINALS:
        return GateResult(False, level, (
            f"{model}.{method}: safety_layer.py returned {level}, which this "
            "gate has no ordinal for. Refused by default deny until "
            "LEVEL_ORDINALS is updated."))

    try:
        check_guards(model, method, args, {})
    except SafetyViolation as violation:
        return GateResult(False, level, str(violation))

    ordinal, ceiling = LEVEL_ORDINALS[level], max_level()
    if ordinal > ceiling:
        return GateResult(False, level, (
            f"{model}.{method} is {level} (ordinal {ordinal}), above this "
            f"server's ceiling ODOO_MCP_MAX_LEVEL={ceiling}. Refused. Say what "
            "the call would change and let the user decide: allowing it means "
            f"restarting the server with ODOO_MCP_MAX_LEVEL={ordinal}."))
    return GateResult(True, level, (
        f"{model}.{method} is {level} (ordinal {ordinal}), within "
        f"ODOO_MCP_MAX_LEVEL={ceiling}."))
