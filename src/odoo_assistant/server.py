#!/usr/bin/env python3
"""odoo-assistant MCP server — process skeleton.

Transport is stdio: stdout carries the JSON-RPC stream and NOTHING else, so
every diagnostic goes to stderr through `logger`. `print()` is banned in this
module. (Residual risk, verified in tests/SPIKE_NOTES.md §5: the SDK ships
OpenTelemetry instrumentation which is silent here because only
`opentelemetry-api` is installed — a host with `opentelemetry-sdk` and
`OTEL_TRACES_EXPORTER=console` would write spans to stdout and corrupt the
stream. The JSON-lines purity assertion in the smoke test is what catches it.)

Tools, resources and prompts are registered in a later milestone (PRD §5B);
this module owns only the server instance, credentials, logging and startup.
"""
import logging
import os
import sys
from pathlib import Path
from typing import NamedTuple

from mcp.server import MCPServer

# The nine Odoo scripts are flat modules that import each other by bare name.
# Their own bootstrap covers the imports *between* them; this one lets THIS
# module import them the same way, both from the repo and from a wheel.
sys.path.insert(0, str(Path(__file__).parent / "odoo_scripts"))

from odoo_client import Odoo, connect  # noqa: E402  (needs the bootstrap above)

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

mcp = MCPServer("odoo-assistant")

_odoo_instance: Odoo | None = None


class _Credentials(NamedTuple):
    base_url: str
    db: str
    user: str
    secret: str


def _credentials() -> _Credentials:
    """Read the connection settings from the environment. No defaults, ever.

    Odoo passes an API key and a password through the same authentication
    slot, so either one works: the key is preferred (Odoo 14+) and the
    password is the legacy fallback for Odoo 13 and earlier (PRD §6B).
    The database and the login are required by the XML-RPC transport.
    """
    base_url = os.environ.get("ODOO_BASE_URL", "")
    db = os.environ.get("ODOO_DB", "")
    user = os.environ.get("ODOO_USER", "")
    secret = os.environ.get("ODOO_API_KEY", "") or os.environ.get("ODOO_PASSWORD", "")

    missing = [
        name
        for name, value in (
            ("ODOO_BASE_URL", base_url),
            ("ODOO_DB", db),
            ("ODOO_USER", user),
            ("ODOO_API_KEY or ODOO_PASSWORD", secret),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing Odoo credentials: " + ", ".join(missing) + ". "
            "Set these environment variables — the server never guesses an "
            "instance. ODOO_API_KEY (Odoo 14+, Settings > Users > API Keys) is "
            "preferred; ODOO_PASSWORD is the legacy fallback for Odoo 13 and "
            "earlier."
        )
    return _Credentials(base_url, db, user, secret)


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
    """Return the shared client, connecting on first use."""
    global _odoo_instance
    if _odoo_instance is None:
        creds = _credentials()
        _odoo_instance = connect(
            base=creds.base_url, db=creds.db, user=creds.user, key=creds.secret
        )
        logger.info(
            "Connected to %s (db=%s, Odoo %s)",
            creds.base_url,
            creds.db,
            _detect_version(_odoo_instance)["serie"],
        )
    return _odoo_instance


def main() -> None:
    """Entry point of the `odoo-assistant` console script."""
    logger.info("Starting odoo-assistant MCP server on stdio")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
