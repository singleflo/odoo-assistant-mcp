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
    tools_evolution,
    tools_read,
    tools_write,
)
from odoo_assistant.server_safety import max_level  # noqa: E402

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
    unsupported. The database and the login are required by the XML-RPC
    transport.
    """
    base_url = os.environ.get("ODOO_BASE_URL", "")
    db = os.environ.get("ODOO_DB", "")
    user = os.environ.get("ODOO_USER", "")
    api_key = os.environ.get("ODOO_API_KEY", "")

    missing = [
        name
        for name, value in (
            ("ODOO_BASE_URL", base_url),
            ("ODOO_DB", db),
            ("ODOO_USER", user),
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

    `allow_write` is the client's declaration of intent, and it is the ONLY
    thing that arms its production guard: `connect()` blocks a
    `PRODUCTION_HOSTS` base URL with `ProductionWriteBlocked` if and only if
    `allow_write` is true. Connecting with the default `False` while the
    server goes on to write through `Writer` disarmed that guard entirely —
    `create_record` against app.persevida.com would have gone straight
    through. So the intent must be declared here, truthfully.

    "Truthfully" is `max_level() >= 1`: the ceiling already decides whether
    this process may execute anything above L0_READ, so it is the honest
    answer to "can this server write at all". A server pinned to
    ODOO_MCP_MAX_LEVEL=0 never writes, declares no write intent, and keeps
    read-only access to a production instance — a hardcoded `True` would have
    broken that legitimate case, since the guard raises at connect time,
    before any tool has a chance to be read-only. `ODOO_ALLOW_PROD_WRITE=yes`
    remains the script's own documented, deliberate escape hatch.
    """
    global _odoo_instance
    if _odoo_instance is None:
        creds = _credentials()
        _odoo_instance = connect(
            allow_write=max_level() >= 1,
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


def _register_all() -> None:
    """Attach every tool and resource to `mcp`: 15 tools and the `odoo://` set.

    Order is free. The one coupling worth naming is already settled: importing
    `tools_evolution` points `explore_module.REF_DIR` at the same directory
    `resources.USER_REFERENCES_DIR` serves, so both agree whichever runs first.
    """
    tools_read.register(mcp)
    tools_write.register(mcp)
    tools_collab.register(mcp)
    tools_evolution.register(mcp)
    resources.register(mcp)


def main() -> None:
    """Entry point of the `odoo-assistant` console script."""
    logger.info("Starting odoo-assistant MCP server on stdio")
    _register_all()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
