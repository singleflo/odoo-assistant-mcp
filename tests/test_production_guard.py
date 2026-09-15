"""The protected-host guard is a CLI-script behaviour the server never arms.

`odoo_client.connect()` refuses a base URL listed in ODOO_MCP_PROTECTED_HOSTS
with `ProductionWriteBlocked` — but only when the CALLER declares write intent:

    if allow_write and _is_production(base): ...

Since 0.2.0 the server never declares it: the safety ceiling that `_get_odoo()`
used to read for its answer is gone, read-only is `ODOO_MCP_ALLOW=none` and is
enforced per call by the gate in `server_safety`, so the connection has nothing
to declare and the guard cannot fire on the server's behalf. The gate refuses
(with a reason an agent can act on) where the old arming produced a second,
weaker authority that fired before any tool could be read-only.
`ProductionWriteBlocked` stays for the nine CLI scripts, which pass
`allow_write=True` deliberately.

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
def stubbed_client(monkeypatch):
    """Given: the `Odoo` constructor stubbed, working credentials in the
    environment, and no protected list unless a test names one — so
    `connect()` runs for real and only the guard decides."""

    def _with_credentials(base_url):
        monkeypatch.setattr(odoo_client, "Odoo", _StubOdoo)
        monkeypatch.delenv("ODOO_MCP_PROTECTED_HOSTS", raising=False)
        monkeypatch.delenv("ODOO_ALLOW_PROD_WRITE", raising=False)
        monkeypatch.setenv("ODOO_BASE_URL", base_url)
        monkeypatch.setenv("ODOO_DB", "mycompany")
        monkeypatch.setenv("ODOO_USER", "tester")
        monkeypatch.setenv("ODOO_API_KEY", "test-key")

    return _with_credentials


def test_the_list_matches_a_bare_hostname_inside_any_url(stubbed_client, monkeypatch):
    """Given a protected entry naming a bare hostname, When a CLI script
    connects with declared write intent to any URL carrying it, Then
    ProductionWriteBlocked is raised naming the host — the entry need not
    repeat the scheme or port (`_is_production` matches on `host in url`).
    ODOO_ALLOW_PROD_WRITE=yes stays the script's own deliberate escape hatch."""
    stubbed_client("https://erp.mycompany.com:443")
    monkeypatch.setenv("ODOO_MCP_PROTECTED_HOSTS", "erp.mycompany.com")

    with pytest.raises(ProductionWriteBlocked) as raised:
        odoo_client.connect(
            allow_write=True,
            base="https://erp.mycompany.com:443",
            key="test-key",
            db="mycompany",
            user="tester",
        )

    assert "erp.mycompany.com" in str(raised.value)

    monkeypatch.setenv("ODOO_ALLOW_PROD_WRITE", "yes")

    odoo = odoo_client.connect(
        allow_write=True,
        base="https://erp.mycompany.com:443",
        key="test-key",
        db="mycompany",
        user="tester",
    )

    assert isinstance(odoo, _StubOdoo)


def test_the_server_connects_without_arming_the_guard(stubbed_client, monkeypatch):
    """Given the operator's own host on the protected list, When the server
    connects, Then it still gets its client: the guard fires only for a
    caller that declares write intent, and `_get_odoo()` no longer declares
    any — read-only is ODOO_MCP_ALLOW=none, enforced per call by the gate."""
    stubbed_client(MY_COMPANY_URL)
    monkeypatch.setenv("ODOO_MCP_PROTECTED_HOSTS", "odoo.mycompany.com")
    monkeypatch.setattr(server, "_odoo_instance", None)

    odoo = server._get_odoo()

    assert odoo.base == MY_COMPANY_URL
