#!/usr/bin/env python3
"""odoo-assistant MCP server — process skeleton.

Transport is stdio: stdout carries the JSON-RPC stream and NOTHING else, so
every diagnostic goes to stderr through `logger`. `print()` is banned in this
module. (Residual risk, verified in tests/SPIKE_NOTES.md §5: the SDK ships
OpenTelemetry instrumentation which is silent here because only
`opentelemetry-api` is installed — a host with `opentelemetry-sdk` and
`OTEL_TRACES_EXPORTER=console` would write spans to stdout and corrupt the
stream. The JSON-lines purity assertion in the smoke test is what catches it.)

Tools and resources live in their own modules and are attached here by
`_register_all()`; this module owns the server instance, credentials, logging
and startup, and no business logic of its own.
"""
import logging
import os
import sys
import threading
from importlib.metadata import PackageNotFoundError, version as _package_version
from pathlib import Path
from typing import NamedTuple

from mcp.server import MCPServer

# The nine Odoo scripts are flat modules that import each other by bare name.
# Their own bootstrap covers the imports *between* them; this one lets THIS
# module import them the same way, both from the repo and from a wheel.
sys.path.insert(0, str(Path(__file__).parent / "odoo_scripts"))

from odoo_client import MissingCredentials, Odoo, connect  # noqa: E402  (needs the bootstrap above)

# These import this module back, for `_get_odoo` at call time. The cycle
# resolves through `sys.modules` only because none of them reads an attribute
# of `server` at import time — keep it that way.
from odoo_assistant import (  # noqa: E402  (cycle: must follow the bootstrap)
    resources,
    tools_collab,
    tools_discuss,
    tools_evolution,
    tools_read,
    tools_write,
)
from odoo_assistant.server_safety import refuse_legacy_environment  # noqa: E402

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

try:
    _VERSION = _package_version("odoo-assistant")
except PackageNotFoundError:  # a source tree that was never installed
    _VERSION = "0+unknown"

# The SDK defaults `version` to "", which is what a host then displays.
mcp = MCPServer("odoo-assistant", version=_VERSION)

_odoo_instance: Odoo | None = None
_odoo_lock = threading.Lock()


class _Credentials(NamedTuple):
    base_url: str
    db: str
    user: str
    api_key: str


def _credentials() -> _Credentials:
    """Read the connection settings from the environment. No defaults, ever.

    An API key is the ONLY accepted secret. Odoo passes keys and passwords
    through the same authentication slot, so a password would technically
    work — which is exactly why it is refused here rather than left to the
    protocol: a key is per-user, revocable and scoped, an account password is
    none of those (PRD non-goal N5, references/SKILL.md rule 7). The client
    itself takes no password either — `connect()` accepts `key` only. The one
    case a password could serve is Odoo ≤13, which this server declares
    unsupported.

    The database and the login are both optional. The client discovers the
    database from the instance itself (`discover_db`): one round trip when
    exactly one database is served — the common case — and a clear error
    naming the candidates when there are several, at which point ODOO_DB must
    name one. Supplied, ODOO_DB skips that probe.

    The **login** is never required. Supplied, it costs one
    `common.authenticate()` round trip. Omitted, `_discover_uid()`
    (`odoo_client.py:250`) reads `res.users.login` for uid 1..59 and keeps the
    one the key answers for, so the login is discovered rather than guessed —
    but that probe costs up to 59 extra round trips and FAILS on an instance
    where the key owner's uid is 60 or higher. Odoo publishes no endpoint that
    maps a key to its owner, which is why discovery has to be a probe: its own
    `authenticate()` looks the user up BY LOGIN before it ever checks the key.
    So ODOO_USER stays worth setting; it is simply not required.
    """
    base_url = os.environ.get("ODOO_BASE_URL", "")
    db = os.environ.get("ODOO_DB", "")
    user = os.environ.get("ODOO_USER", "")
    api_key = os.environ.get("ODOO_API_KEY", "")

    missing = [
        name
        for name, value in (
            ("ODOO_BASE_URL", base_url),
            ("ODOO_API_KEY", api_key),
        )
        if not value
    ]
    if missing:
        raise MissingCredentials(
            "Missing Odoo credentials: " + ", ".join(missing) + ". "
            "Set these environment variables — the server never guesses an "
            "instance. ODOO_API_KEY is an API key (Odoo 14+, Settings > Users "
            "> API Keys > New), never an account password: passwords are not "
            "accepted."
        )
    return _Credentials(base_url, db, user, api_key)


def _detect_version(odoo: Odoo) -> dict[str, object]:
    """Report the Odoo release of the connected instance (PRD §7C).

    The client exposes this through `info()` — there is no `version()` method
    on `Odoo`. `info()` is what calls `/xmlrpc/2/common` version() and maps
    `server_serie` ("18.0") onto its `odoo_version` key.
    """
    serie = str(odoo.info().get("odoo_version") or "")
    parts = serie.split(".")
    return {
        "serie": serie,
        "major": int(parts[0]) if parts[0].isdigit() else 0,
        "minor": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0,
    }


def _get_odoo() -> Odoo:
    """Return the shared client, connecting on first use.

    The client's `connect()` takes a write-intent flag that arms its
    protected-host guard, and this server passes nothing: whether a call may
    run is decided per call by the gate — read-only is `ODOO_MCP_ALLOW=none`,
    enforced by `server_safety.gate` before anything reaches Odoo — so the
    connection has nothing to declare. `ProductionWriteBlocked` stays a
    CLI-script behaviour; the server never arms it.

    Thread-safe because `main()` warms the connection up in a background
    thread: a tool call arriving mid-connect waits on `_odoo_lock` for the
    SAME client instead of opening a second connection.
    """
    global _odoo_instance
    with _odoo_lock:
        if _odoo_instance is None:
            creds = _credentials()
            _odoo_instance = connect(
                base=creds.base_url,
                db=creds.db,
                user=creds.user,
                key=creds.api_key,
            )
            logger.info(
                "Connected to %s (db=%s, Odoo %s)",
                creds.base_url,
                creds.db,
                _detect_version(_odoo_instance)["serie"],
            )
        return _odoo_instance


def _warm_up() -> None:
    """Connect in the background so the first tool call does not pay for it.

    The catch is broad on purpose: missing credentials, an unreachable host,
    TLS — none of them may take the server down, because a host that launched
    us without credentials still gets a running server whose tools explain
    exactly which variable is missing when called.
    """
    try:
        _get_odoo()
    except Exception as exc:
        logger.warning("Startup connection failed: %s — tools will retry and report", exc)


def _register_all() -> None:
    """Attach every tool and resource to `mcp`: 19 tools and the `odoo://` set.

    Order is free. The one coupling worth naming is already settled: importing
    `tools_evolution` points `explore_module.REF_DIR` at the same directory
    `resources.USER_REFERENCES_DIR` serves, so both agree whichever runs first.
    """
    tools_read.register(mcp)
    tools_write.register(mcp)
    tools_collab.register(mcp)
    tools_discuss.register(mcp)
    tools_evolution.register(mcp)
    resources.register(mcp)


def main() -> None:
    """Entry point of the `odoo-assistant` console script."""
    # A removed variable in the environment refuses startup (RuntimeError
    # propagates: non-zero exit, traceback on stderr) — a stale read-only
    # ceiling must never become a writing server by being ignored.
    refuse_legacy_environment()
    logger.info("Starting odoo-assistant MCP server on stdio")
    _register_all()
    # Daemon and never awaited: the connect can cost up to 59 uid probes,
    # and the host's initialize handshake must be answered first.
    threading.Thread(target=_warm_up, daemon=True).start()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
