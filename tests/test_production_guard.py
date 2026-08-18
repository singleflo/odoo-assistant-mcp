"""The protected-host guard, as armed by `server._get_odoo()`.

The list of protected hosts lives in the ENVIRONMENT, not in the code:
`ODOO_MCP_PROTECTED_HOSTS`, comma-separated, empty by default. A generic
install therefore works against any instance — an operator opts a host into
protection by naming it, which is the only way a host anyone installs against
can end up on a list the package ships.

`odoo_client.connect()` refuses a listed base URL with
`ProductionWriteBlocked` — but only when it is told a write is intended:

    if allow_write and _is_production(base): ...

So the guard is not something the client applies to us; it is something we
arm, and connecting without `allow_write` silently disarms it while the
server keeps writing through `Writer`. These tests pin the arming.

No test here touches the network. The guard sits above the `Odoo(...)`
construction, and every case that gets PAST the guard would stop at the
constructor, so the constructor is the seam these tests stub: `connect()`
itself, guard included, runs for real.
"""
import pytest

from odoo_assistant import server

import odoo_client  # noqa: E402  (importing `server` above runs its bootstrap)
from odoo_client import ProductionWriteBlocked  # noqa: E402

MY_COMPANY_URL = "https://odoo.mycompany.com"


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
    """Given: credentials for a chosen instance, no cached client, no network,
    and no protected list unless a test names one."""
    monkeypatch.setattr(odoo_client, "Odoo", _StubOdoo)
    monkeypatch.setattr(server, "_odoo_instance", None)
    monkeypatch.delenv("ODOO_ALLOW_PROD_WRITE", raising=False)
    monkeypatch.delenv("ODOO_MCP_PROTECTED_HOSTS", raising=False)
    monkeypatch.delenv("ODOO_MCP_MAX_LEVEL", raising=False)

    def _configure(base_url, protected=""):
        monkeypatch.setenv("ODOO_BASE_URL", base_url)
        monkeypatch.setenv("ODOO_DB", "mycompany")
        monkeypatch.setenv("ODOO_USER", "tester")
        monkeypatch.setenv("ODOO_API_KEY", "test-key")
        if protected:
            monkeypatch.setenv("ODOO_MCP_PROTECTED_HOSTS", protected)

    return _configure


def test_no_host_is_protected_unless_the_operator_names_it(connecting_to):
    """Given a fresh install with no ODOO_MCP_PROTECTED_HOSTS, When a writing
    server connects anywhere, Then nothing refuses it — the package ships no
    host list of its own, so any instance a user installs against works."""
    connecting_to(MY_COMPANY_URL)

    odoo = server._get_odoo()

    assert odoo.base == MY_COMPANY_URL


def test_a_host_named_in_the_environment_is_blocked(connecting_to):
    """Given the operator protected their own host, When a writing server
    points at it, Then connecting is refused and the error names the host."""
    connecting_to(MY_COMPANY_URL, protected="odoo.mycompany.com")

    with pytest.raises(ProductionWriteBlocked) as raised:
        server._get_odoo()

    assert MY_COMPANY_URL in str(raised.value)


def test_the_documented_escape_hatch_lets_a_protected_host_through(
    connecting_to, monkeypatch
):
    """ODOO_ALLOW_PROD_WRITE=yes is a deliberate, out-of-band override."""
    connecting_to(MY_COMPANY_URL, protected="odoo.mycompany.com")
    monkeypatch.setenv("ODOO_ALLOW_PROD_WRITE", "yes")

    odoo = server._get_odoo()

    assert odoo.base == MY_COMPANY_URL


def test_a_read_only_server_may_still_reach_a_protected_host(
    connecting_to, monkeypatch
):
    """ODOO_MCP_MAX_LEVEL=0 writes nothing, so it declares no write intent."""
    connecting_to(MY_COMPANY_URL, protected="odoo.mycompany.com")
    monkeypatch.setenv("ODOO_MCP_MAX_LEVEL", "0")

    odoo = server._get_odoo()

    assert odoo.base == MY_COMPANY_URL


def test_the_list_matches_a_bare_hostname_inside_any_url(connecting_to):
    """Given the guard predates on `host in url`, When a protected entry
    names a bare hostname, Then any URL carrying it is refused — the entry
    need not repeat the scheme or port."""
    connecting_to("https://erp.mycompany.com:443", protected="erp.mycompany.com")

    with pytest.raises(ProductionWriteBlocked):
        server._get_odoo()
