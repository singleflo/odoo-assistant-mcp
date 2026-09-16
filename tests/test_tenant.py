"""The tenant seam: one authenticated caller decides the client AND the gate.

`tenant.py` carries a ContextVar the remote middleware binds per request; the
two things that follow it are `server._get_odoo()` (which client) and
`server_safety`'s three policy readers (what may run). Case (a) here is the
characterisation baseline: with NO tenant bound, both behave byte-identically
to the plain environment server — the env path stays first-class.

The gate is the only enforcement point, so the assertions below check the
REASON strings, not just the allowed booleans: an agent has to relay them.
"""
import threading

import pytest

from odoo_assistant import server, server_safety, tenant
from odoo_assistant.server_safety import DEFAULT_DENY, gate


@pytest.fixture(autouse=True)
def no_tenant_leak():
    """Given a fresh context for every test, whatever a test bound is gone.

    A bind() without a reset would leak into every later test on this thread —
    contextvars live in the thread's context, not in the test frame — so the
    teardown forces the variable back to None.
    """
    tenant._current.set(None)
    yield
    tenant._current.set(None)


@pytest.fixture(autouse=True)
def clean_lists(monkeypatch):
    """Given none of the gate's variables in the environment, unless a test sets one."""
    for name in ("ODOO_MCP_ALLOW", "ODOO_MCP_DENY",
                 "ODOO_MCP_ALLOW_UNLINK", "ODOO_MCP_MAX_LEVEL"):
        monkeypatch.delenv(name, raising=False)


class _FakeClient:
    """Stands where `Odoo` stands: `_get_odoo` reads `info()` right after."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def info(self):
        return {"odoo_version": "18.0"}


# ------------------------------------------------- (a) the env path, unchanged
def test_no_tenant_gate_behaviour_is_byte_identical(monkeypatch):
    """Given no tenant bound, When the gate decides, Then every answer is the
    historical one — including the exact env-flavoured reason sentences."""
    assert gate("res.partner", "write", [1], {"name": "ACME"}).allowed is True

    refused = gate("res.partner", "unlink", [1])
    assert refused.allowed is False
    assert refused.reason == (
        "res.partner.unlink: deletion is the one action that cannot be "
        "undone. It is granted only by ODOO_MCP_ALLOW_UNLINK=yes; the "
        "ODOO_MCP_ALLOW and ODOO_MCP_DENY lists can never grant it.")

    monkeypatch.setenv("ODOO_MCP_ALLOW", "none")
    read_only = gate("res.partner", "create", None, {"name": "ACME"})
    assert read_only.allowed is False
    assert read_only.reason == (
        "res.partner.create: this is a read-only server "
        "(ODOO_MCP_ALLOW=none). Remove the variable, or set "
        "ODOO_MCP_ALLOW to the entries you want.")


def test_no_tenant_readers_are_the_environment(monkeypatch):
    """Given no tenant bound, When the three policy readers are consulted,
    Then they read the environment exactly as before."""
    assert server_safety.allowed_methods() == "*"
    assert server_safety.denied_methods() == set(DEFAULT_DENY)

    monkeypatch.setenv("ODOO_MCP_ALLOW_UNLINK", "yes")
    assert server_safety.unlink_allowed() is True

    monkeypatch.setenv("ODOO_MCP_ALLOW", "create,write")
    assert server_safety.allowed_methods() == {"create", "write"}

    monkeypatch.setenv("ODOO_MCP_DENY", "unlink")
    assert server_safety.denied_methods() == {"unlink"}


def test_no_tenant_get_odoo_is_the_env_singleton(monkeypatch):
    """Given no tenant bound and no client yet, When _get_odoo runs twice,
    Then the env credentials drive one connect call and both calls share it —
    the seam must not have changed the plain stdio path."""
    calls = []

    def fake_connect(**kwargs):
        calls.append(kwargs)
        return _FakeClient(**kwargs)

    monkeypatch.setenv("ODOO_BASE_URL", "http://odoo.invalid:8069")
    monkeypatch.setenv("ODOO_API_KEY", "key")
    monkeypatch.setattr(server, "_odoo_instance", None)
    monkeypatch.setattr(server, "connect", fake_connect)

    first = server._get_odoo()
    second = server._get_odoo()

    assert first is second
    assert len(calls) == 1
    assert calls[0]["base"] == "http://odoo.invalid:8069"
    assert calls[0]["key"] == "key"


# ---------------------------------------------------- the tenant dataclass
def test_a_policy_other_than_read_or_standard_is_refused():
    """Given a typo'd policy, When a Tenant is constructed, Then the
    dataclass refuses it — an unvalidated literal would silently fall through
    to the environment in every reader."""
    with pytest.raises((TypeError, ValueError)):
        tenant.Tenant("u1", "http://x", "k", "db", "readonly")


# --------------------------------------------------- (b) the read policy
def test_read_tenant_refuses_writes_with_the_read_only_reason():
    """Given a bound read tenant, When a write is gated, Then it is refused
    and the reason tells the agent how to actually get the permission."""
    token = tenant.bind(tenant.Tenant(
        "u1", "http://x", "k", "db", "read"))
    try:
        decision = gate("res.partner", "write", [1], {"name": "x"})

        assert decision.allowed is False
        assert "read-only" in decision.reason
        assert "authorised as read-only" in decision.reason
        assert "reconnect and choose the standard policy" in decision.reason
        assert "Remove the variable" not in decision.reason
        assert server_safety.allowed_methods() == "none"
        assert gate("res.partner", "search_read", []).allowed is True
    finally:
        tenant.reset(token)


# ----------------------------------------------- (c) the standard policy
def test_standard_tenant_ignores_the_environment():
    """Given a bound standard tenant and an env that would make the server
    read-only, When write / action_cancel / unlink are gated, Then the
    package defaults decide — the env lists are not consulted at all."""
    monkeypatch_setenv = pytest.MonkeyPatch()
    try:
        monkeypatch_setenv.setenv("ODOO_MCP_ALLOW", "none")
        monkeypatch_setenv.setenv("ODOO_MCP_ALLOW_UNLINK", "yes")
        token = tenant.bind(tenant.Tenant(
            "u1", "http://x", "k", "db", "standard"))

        assert server_safety.allowed_methods() == "*"
        assert server_safety.denied_methods() == set(DEFAULT_DENY)
        assert server_safety.unlink_allowed() is False
        assert gate("res.partner", "write", [1], {"name": "x"}).allowed is True

        cancelled = gate("sale.order", "action_cancel", [1])
        assert cancelled.allowed is False
        assert "ODOO_MCP_DENY" in cancelled.reason
        assert "'action_cancel'" in cancelled.reason

        deleted = gate("res.partner", "unlink", [1])
        assert deleted.allowed is False
        assert ("Deletion is never available on the hosted server; use a "
                "local install with ODOO_MCP_ALLOW_UNLINK=yes.") in deleted.reason
    finally:
        tenant.reset(token)
        monkeypatch_setenv.undo()


# ---------------------------------------------- (d) one client per subject
def test_odoo_for_is_cached_per_subject(monkeypatch):
    """Given two calls for the same subject, When odoo_for resolves each,
    Then connect ran once and both calls share the client; a second subject
    mints its own."""
    calls = []
    monkeypatch.setattr(tenant, "connect", lambda **kw: calls.append(kw) or object())

    u1 = tenant.Tenant("u1", "http://x", "k1", "db", "standard")
    u1_again = tenant.Tenant("u1", "http://x", "k1", "db", "standard")
    u2 = tenant.Tenant("u2", "http://y", "k2", "db", "read")

    first = tenant.odoo_for(u1)
    assert tenant.odoo_for(u1_again) is first
    assert len(calls) == 1
    assert calls[0] == {"base": "http://x", "db": "db", "user": "", "key": "k1"}

    assert tenant.odoo_for(u2) is not first
    assert len(calls) == 2


def test_same_subject_with_a_different_database_gets_a_new_client(monkeypatch):
    calls = []
    monkeypatch.setattr(tenant, "_clients", {})
    monkeypatch.setattr(
        tenant, "connect", lambda **kw: calls.append(kw) or object())

    first = tenant.odoo_for(tenant.Tenant(
        "u-db", "http://x", "k", "db-a", "standard"))
    second = tenant.odoo_for(tenant.Tenant(
        "u-db", "http://x", "k", "db-b", "standard"))

    assert second is not first
    assert [call["db"] for call in calls] == ["db-a", "db-b"]


def test_forget_drops_every_cached_client_for_the_subject(monkeypatch):
    calls = []
    monkeypatch.setattr(tenant, "_clients", {})
    monkeypatch.setattr(
        tenant, "connect", lambda **kw: calls.append(kw) or object())
    tenant.odoo_for(tenant.Tenant(
        "u-forget", "http://x", "k", "db-a", "standard"))
    tenant.odoo_for(tenant.Tenant(
        "u-forget", "http://x", "k", "db-b", "standard"))

    tenant.forget("u-forget")
    tenant.odoo_for(tenant.Tenant(
        "u-forget", "http://x", "k", "db-a", "standard"))

    assert len(calls) == 3


def test_store_delete_tenant_also_forgets_its_live_client(
        monkeypatch, tmp_path):
    pytest.importorskip("cryptography.fernet")
    from cryptography.fernet import Fernet
    from odoo_assistant.remote.store import Store

    calls = []
    monkeypatch.setattr(tenant, "_clients", {})
    monkeypatch.setattr(
        tenant, "connect", lambda **kw: calls.append(kw) or object())
    connected = tenant.Tenant(
        "u-delete", "http://x", "k", "db", "standard")
    store = Store(tmp_path / "remote.db", Fernet.generate_key().decode())
    store.init()
    store.put_tenant(connected)
    tenant.odoo_for(connected)

    store.delete_tenant("u-delete")
    tenant.odoo_for(connected)

    assert len(calls) == 2


# ------------------------------------------- (e) isolation between tenants
def test_two_tenants_in_two_threads_resolve_two_clients(monkeypatch):
    """Given a tenant bound inside each of two threads, When each asks the
    server for its client, Then each gets its own — and a reset in the
    original thread puts the environment back in charge."""
    clients = {}

    def fake_connect(**kwargs):
        return ("client", kwargs["base"], kwargs["key"])

    monkeypatch.setattr(server, "_odoo_instance", None)
    # `odoo_for` resolves `connect` from the TENANT module's globals — that is
    # the seam to patch here, not `server.connect`.
    monkeypatch.setattr(tenant, "connect", fake_connect)

    def work(name, policy):
        tok = tenant.bind(tenant.Tenant(
            name, f"http://{name}", f"key-{name}", "db", policy))
        try:
            clients[name] = server._get_odoo()
        finally:
            tenant.reset(tok)

    threads = [
        threading.Thread(target=work, args=("alice", "standard")),
        threading.Thread(target=work, args=("bob", "read")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert clients["alice"] == ("client", "http://alice", "key-alice")
    assert clients["bob"] == ("client", "http://bob", "key-bob")
    assert clients["alice"] != clients["bob"]


def test_after_reset_the_environment_decides_again(monkeypatch):
    """Given a bind and its reset, When current() and the gate are consulted,
    Then the tenant is gone and the env policy answers — a stale binding must
    never outlive its request."""
    token = tenant.bind(tenant.Tenant("u1", "http://x", "k", "db", "read"))
    assert tenant.current() is not None
    tenant.reset(token)

    assert tenant.current() is None
    monkeypatch.setenv("ODOO_MCP_ALLOW", "none")
    decision = gate("res.partner", "write", [1], {"name": "x"})
    assert "ODOO_MCP_ALLOW=none" in decision.reason
    assert "authorised as read-only" not in decision.reason


def test_the_tenants_login_is_what_the_client_authenticates_with(monkeypatch):
    """Given a tenant that recorded the key owner's login, When its client is
    built, Then the login is handed to connect — the uid is a parameter of the
    XML-RPC call and Odoo derives the login from it, never the other way
    round, so without this the client is back to probing uid 1 to 59."""
    calls = []
    monkeypatch.setattr(tenant, "_clients", {})
    monkeypatch.setattr(
        tenant, "connect", lambda **kw: calls.append(kw) or object())

    tenant.odoo_for(tenant.Tenant(
        "u-login", "http://x", "k", "db", "standard", "jane@acme.com"))

    assert calls[0]["user"] == "jane@acme.com"


def test_a_changed_login_is_not_served_from_the_cached_client(monkeypatch):
    """Re-consenting with the login filled in must reconnect: the cached
    client authenticated as whoever the probe found, which on the instance
    that needed the field is nobody."""
    calls = []
    monkeypatch.setattr(tenant, "_clients", {})
    monkeypatch.setattr(
        tenant, "connect", lambda **kw: calls.append(kw) or object())

    first = tenant.odoo_for(tenant.Tenant(
        "u-same", "http://x", "k", "db", "standard"))
    second = tenant.odoo_for(tenant.Tenant(
        "u-same", "http://x", "k", "db", "standard", "jane@acme.com"))

    assert second is not first
    assert [call["user"] for call in calls] == ["", "jane@acme.com"]
