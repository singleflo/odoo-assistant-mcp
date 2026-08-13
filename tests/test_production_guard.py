"""The production-host guard, as armed by `server._get_odoo()`.

`odoo_client.connect()` refuses a `PRODUCTION_HOSTS` base URL with
`ProductionWriteBlocked` — but only when it is told a write is intended:

    if allow_write and _is_production(base): ...        (odoo_client.py:516)

So the guard is not something the client applies to us; it is something we
arm, and connecting without `allow_write` silently disarms it while the
server keeps writing through `Writer`. These tests pin the arming.

No test here touches the network. The guard sits at line 516, above the
`Odoo(...)` construction at line 522, and the `discover_db()` call it follows
runs only `if not db` — the server always supplies `ODOO_DB`. Every case that
gets PAST the guard would therefore stop at the constructor, so the
constructor is the seam these tests stub: `connect()` itself, guard included,
runs for real.
"""
import pytest

from odoo_assistant import server

import odoo_client  # noqa: E402  (importing `server` above runs its bootstrap)
from odoo_client import ProductionWriteBlocked  # noqa: E402

PRODUCTION_URL = "https://app.persevida.com"
DEV_URL = "http://persevida-dev18.invalid:8069"


class _StubOdoo:
    """Stands where the real client stands once `connect()` has decided.

    Reaching it at all is the assertion: it means the guard let the
    connection through.
    """

    def __init__(self, base, key, db, user):
        self.base, self.key, self.db, self.user = base, key, db, user

    def info(self):
        return {"odoo_version": "18.0"}


@pytest.fixture
def connecting_to(monkeypatch):
    """Given: credentials for a chosen instance, no cached client, no network."""
    monkeypatch.setattr(odoo_client, "Odoo", _StubOdoo)
    monkeypatch.setattr(server, "_odoo_instance", None)
    monkeypatch.delenv("ODOO_ALLOW_PROD_WRITE", raising=False)
    monkeypatch.delenv("ODOO_MCP_MAX_LEVEL", raising=False)

    def _configure(base_url):
        monkeypatch.setenv("ODOO_BASE_URL", base_url)
        monkeypatch.setenv("ODOO_DB", "persevida_dev18")
        monkeypatch.setenv("ODOO_USER", "tester")
        monkeypatch.setenv("ODOO_API_KEY", "test-key")

    return _configure


def test_production_host_is_blocked_at_the_default_ceiling(connecting_to):
    """When a writing server points at production, connecting is refused."""
    connecting_to(PRODUCTION_URL)

    with pytest.raises(ProductionWriteBlocked) as raised:
        server._get_odoo()

    assert PRODUCTION_URL in str(raised.value)


def test_the_documented_escape_hatch_lets_production_through(
    connecting_to, monkeypatch
):
    """ODOO_ALLOW_PROD_WRITE=yes is a deliberate, out-of-band override."""
    connecting_to(PRODUCTION_URL)
    monkeypatch.setenv("ODOO_ALLOW_PROD_WRITE", "yes")

    odoo = server._get_odoo()

    assert odoo.base == PRODUCTION_URL


def test_a_read_only_server_may_still_reach_production(connecting_to, monkeypatch):
    """ODOO_MCP_MAX_LEVEL=0 writes nothing, so it declares no write intent."""
    connecting_to(PRODUCTION_URL)
    monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", "0")

    odoo = server._get_odoo()

    assert odoo.base == PRODUCTION_URL


@pytest.mark.parametrize("ceiling", ["0", "3", "4"])
def test_a_non_production_host_is_never_blocked(connecting_to, monkeypatch, ceiling):
    """The guard reads the host, not the ceiling: dev is open at every level."""
    connecting_to(DEV_URL)
    monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", ceiling)

    odoo = server._get_odoo()

    assert odoo.base == DEV_URL
