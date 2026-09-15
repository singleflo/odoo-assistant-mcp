"""The hosted server end to end: routes, OAuth, tenant binding over /mcp.

Everything drives the REAL `build_app` surface through `TestClient` as a
context manager — the session manager only starts in lifespan (ASGITransport
never runs it and /mcp dies with RuntimeError there). The two external seams
are patched exactly where the server resolves them:

* `odoo_assistant.remote.consent._verify_isolated` + the consent module's
  `socket.getaddrinfo` — the consent page's credential verification and
  SSRF guard (the verifier itself runs in a spawn child, so the tests pin
  the parent-side seam);
* `odoo_assistant.tenant.connect` — the per-tenant Odoo client minted on the
  first tool call (patch THIS, never server.connect).

The adversarial core is the two-tenant interleave: token A reaches fake Odoo
A, token B reaches fake Odoo B, on one shared app instance — proof that no
cross-binding survives stateless HTTP.

Needs the `remote` extra (`uv sync --extra remote`).
"""
import base64
import hashlib
import json
import secrets
import socket
import sqlite3
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import anyio
import pytest
from starlette.testclient import TestClient

pytest.importorskip(
    "cryptography.fernet",
    reason="the store needs the [remote] extra: uv sync --extra remote")

# noqa: E402 - the gate above must run first, cryptography is remote-only
from cryptography.fernet import Fernet  # noqa: E402
from mcp.server.auth.provider import AccessToken  # noqa: E402

import odoo_assistant.tenant as tenant_module  # noqa: E402
import odoo_assistant.tools_evolution as tools_evolution  # noqa: E402
from odoo_assistant import paths  # noqa: E402
from odoo_assistant.odoo_scripts import explore_module  # noqa: E402
from odoo_assistant.remote import consent, files  # noqa: E402
from odoo_assistant.remote import app as remote_app  # noqa: E402
from odoo_assistant.remote.app import RemoteSettings, build_app  # noqa: E402
from odoo_assistant.remote.auth import OdooAssistantAuthProvider  # noqa: E402
from odoo_assistant.remote.store import Store, key_hash  # noqa: E402
from odoo_assistant.server import _VERSION  # noqa: E402
from odoo_assistant.tools_collab import _tenant_dir  # noqa: E402

PUBLIC_URL = "http://localhost:8000"
MCP_URL = f"{PUBLIC_URL}/mcp"
REDIRECT_URI = "http://localhost:1/callback"
ODOO_A = "https://acme-a.example"
ODOO_B = "https://acme-b.example"
API_KEY = "the-key-the-user-typed"
DB = "acme-prod-1234567"
COUNT_A, COUNT_B = 11, 22

_TENANT_ENV_VARS = ("ODOO_BASE_URL", "ODOO_API_KEY", "ODOO_DB", "ODOO_USER")
_AUTH_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """The hosted server refuses to start with a tenant's Odoo env present;
    a developer shell exporting one would otherwise fail every build."""
    for name in _TENANT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _fresh_tenant_cache(monkeypatch):
    monkeypatch.setattr(tenant_module, "_clients", {})


@pytest.fixture(autouse=True)
def _own_data_root(monkeypatch):
    """build_app pins the process-wide data root; a stale pin from another
    test must never leak into the next one.

    Clearing the pin is not enough: `tools_evolution.register()` DERIVES
    `explore_module.REF_DIR` from it, and that derived global keeps pointing
    at the dead tmp_path once the pin is gone. Re-run the redirect after
    clearing, so the next test sees the default root."""
    monkeypatch.setattr(paths, "_data_dir_override", None, raising=False)
    yield
    _restore_default_data_root(monkeypatch)


def _restore_default_data_root(monkeypatch):
    """Clear the pin AND re-derive every global derived from it."""
    monkeypatch.setattr(paths, "_data_dir_override", None, raising=False)
    tools_evolution._redirect_references()


class ConsentFakeConnect:
    """consent._verify_isolated: records the credentials consent hands over
    and always verifies fine. The real verifier runs in a spawn child that
    re-imports the module fresh, so the parent-side seam is what a test can
    pin; the SSRF guard still runs in-process and needs the fake resolver."""

    def __init__(self):
        self.calls = []

    def __call__(self, odoo_url, db, api_key):
        self.calls.append({
            "base": odoo_url, "db": db, "user": "", "key": api_key})
        return consent._Verified("ok", "", None)


@pytest.fixture
def consent_connect(monkeypatch):
    recorder = ConsentFakeConnect()
    monkeypatch.setattr(consent, "_verify_isolated", recorder)

    def fake_getaddrinfo(host, port=None, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(
        consent.socket, "getaddrinfo", fake_getaddrinfo)
    return recorder


class TenantFakeOdoo:
    """The per-tenant Odoo client double; knows which instance it is."""

    def __init__(self, base_url, count):
        self.base_url = base_url
        self.count = count
        self.searches = []

    def search_count(self, model, domain, context=None):
        self.searches.append((model, domain, context))
        return self.count


@pytest.fixture
def tenant_odoos(monkeypatch):
    """tenant.connect dispatches on base_url so each tenant reaches ITS fake."""
    fakes = {ODOO_A: TenantFakeOdoo(ODOO_A, COUNT_A),
             ODOO_B: TenantFakeOdoo(ODOO_B, COUNT_B)}

    def fake_connect(**kw):
        return fakes[kw["base"]]

    monkeypatch.setattr(tenant_module, "connect", fake_connect)
    return fakes


def make_settings(tmp_path: Path, **overrides) -> RemoteSettings:
    base = RemoteSettings(
        public_url=PUBLIC_URL, secret_key=Fernet.generate_key().decode(),
        host="0.0.0.0", port=8000, openai_challenge=None,
        publisher="the odoo-assistant maintainers",
        support_email="https://github.com/singleflo/odoo-assistant-mcp/issues",
        data_dir=tmp_path, allow_private_targets=False)
    return replace(base, **overrides)


def make_client(tmp_path, **overrides) -> TestClient:
    return TestClient(build_app(make_settings(tmp_path, **overrides)),
                      base_url=PUBLIC_URL, follow_redirects=False)


def _pkce():
    verifier = base64.urlsafe_b64encode(
        secrets.token_bytes(32)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def _rpc_body(method, params=None, id=1):
    body = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def _rpc_result(response):
    """The JSON-RPC payload of a /mcp answer (SSE-framed or plain JSON)."""
    if "text/event-stream" in response.headers.get("content-type", ""):
        for line in response.text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[len("data: "):])
        pytest.fail(f"no SSE data frame in: {response.text[:200]!r}")
    return response.json()


def _full_token(client, *, odoo_url, api_key=API_KEY, db=DB) -> str:
    """register -> authorize -> consent -> token; returns the access token."""
    registered = client.post("/register", json={
        "redirect_uris": [REDIRECT_URI],
        "client_name": "test host",
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    })
    assert registered.status_code == 201, registered.text
    client_id = registered.json()["client_id"]

    verifier, challenge = _pkce()
    asked = client.get("/authorize", params={
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "st-1",
        "scope": "odoo",
        "resource": MCP_URL,
    })
    assert asked.status_code == 302, asked.text
    req = parse_qs(urlparse(asked.headers["location"]).query)["req"][0]

    consented = client.post("/consent", data={
        "req": req, "odoo_url": odoo_url, "api_key": api_key,
        "db": db, "policy": "read"})
    assert consented.status_code == 302, consented.text
    code = parse_qs(urlparse(consented.headers["location"]).query)["code"][0]

    exchanged = client.post("/token", data={
        "grant_type": "authorization_code", "code": code,
        "client_id": client_id, "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI})
    assert exchanged.status_code == 200, exchanged.text
    return exchanged.json()["access_token"]


# ------------------------------------------------------------------- (1, 2)
def test_health_reports_the_server_version(tmp_path):
    with make_client(tmp_path) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": _VERSION}


def test_build_app_uses_a_stateless_session_manager(tmp_path):
    app = build_app(make_settings(tmp_path))

    assert app.state.session_manager.stateless is True


def test_reference_dir_does_not_survive_a_custom_data_root(tmp_path, monkeypatch):
    """Given build_app pinned a custom data root, When the pin is cleared,
    Then explore_module.REF_DIR resolves under the DEFAULT root again —
    clearing the pin alone leaves it pointing at the dead tmp_path, which
    failed the next test that read it."""
    build_app(make_settings(tmp_path))
    assert str(explore_module.REF_DIR).startswith(str(tmp_path))

    _restore_default_data_root(monkeypatch)

    assert str(explore_module.REF_DIR).startswith(str(paths.data_dir()))


def test_one_data_root_build_app_publish_and_tenant_tmp_share_it(tmp_path):
    """Given build_app pinned a custom data root, When a file is published
    and a tool resolves its per-tenant scratch directory, Then both sit
    under the SAME pinned root — files.configure_data_dir's captured global
    used to answer a different, stale directory."""
    root = tmp_path / "chosen-root"
    build_app(make_settings(root))
    payload = root / "seed.txt"
    payload.write_bytes(b"seed")

    link = files.publish(payload, "t_oneroot", public_url=PUBLIC_URL)

    assert (root / "files" / "t_oneroot").is_dir()
    work = _tenant_dir("t_oneroot")
    assert work.parent == root / "files" / "t_oneroot" / "tmp"
    token = link["url"].rsplit("/", 1)[1]
    assert (root / "files" / "t_oneroot" / token / "seed.txt").exists()


def test_revoking_the_last_token_purges_files_and_references(
        tmp_path, consent_connect):
    """Given a tenant with a published file and generated references, When
    its last token is revoked, Then the tenant row AND both disk trees are
    gone — retention must match what the privacy page promises."""
    settings = make_settings(tmp_path)
    with TestClient(build_app(settings), base_url=PUBLIC_URL,
                    follow_redirects=False) as client:
        token = _full_token(client, odoo_url=ODOO_A)
        store = Store(settings.data_dir / "remote.db", settings.secret_key)
        subject = store.find_tenant_by_key_hash(
            key_hash(ODOO_A, API_KEY)).subject
        file_dir = tmp_path / "files" / subject / "tok"
        file_dir.mkdir(parents=True)
        (file_dir / "invoice.pdf").write_bytes(b"%PDF fake")
        ref_dir = tmp_path / "references" / subject
        ref_dir.mkdir(parents=True)
        (ref_dir / "res.sale.order.md").write_text("generated")
        provider = OdooAssistantAuthProvider(store, PUBLIC_URL)

        anyio.run(provider.revoke_token, AccessToken(
            token=token, client_id="test", scopes=[], expires_at=0,
            subject=subject))

    assert store.get_tenant(subject) is None
    assert not (tmp_path / "files" / subject).exists()
    assert not (tmp_path / "references" / subject).exists()


def test_the_retention_sweep_runs_periodically_not_only_at_startup(
        tmp_path, monkeypatch):
    """Given the lifespan running with a shortened interval, When a short
    window passes, Then the sweep fired repeatedly — the old code purged
    exactly once, at startup, and never again."""
    calls = []
    monkeypatch.setattr(
        remote_app, "_sweep_once", lambda store: calls.append(1))
    monkeypatch.setattr(remote_app, "SWEEP_SECONDS", 0.1)

    with make_client(tmp_path):
        after_startup = len(calls)
        time.sleep(0.45)

    assert after_startup == 1
    assert len(calls) - after_startup >= 2


def test_startup_sweep_purges_an_idle_tenants_artifacts(tmp_path):
    """Given a tenant idle past the window with files and references on
    disk, When the lifespan runs its startup sweep, Then the tenant row and
    BOTH artifact trees are gone."""
    settings = make_settings(tmp_path)
    seed = Store(settings.data_dir / "remote.db", settings.secret_key)
    seed.init()
    seed.put_tenant(
        tenant_module.Tenant("t_idle", ODOO_A, API_KEY, DB, "read"))
    with sqlite3.connect(settings.data_dir / "remote.db") as db:
        db.execute(
            "UPDATE tenants SET last_used_at = ? WHERE subject = 't_idle'",
            ((datetime.now(timezone.utc) - timedelta(days=91)).isoformat(),))
    file_dir = tmp_path / "files" / "t_idle" / "tok"
    file_dir.mkdir(parents=True)
    (file_dir / "old.pdf").write_bytes(b"%PDF fake")
    ref_dir = tmp_path / "references" / "t_idle"
    ref_dir.mkdir(parents=True)
    (ref_dir / "old.md").write_text("generated")

    with make_client(tmp_path):  # the lifespan runs the startup sweep
        pass

    assert seed.get_tenant("t_idle") is None
    assert not (tmp_path / "files" / "t_idle").exists()
    assert not (tmp_path / "references" / "t_idle").exists()


def test_build_app_configures_files_to_use_its_data_directory(tmp_path):
    build_app(make_settings(tmp_path))
    payload = tmp_path / "configured.txt"
    payload.write_bytes(b"configured")

    files.publish(payload, "t_configured", public_url=PUBLIC_URL)

    assert any((tmp_path / "files" / "t_configured").iterdir())


def test_main_prints_help_without_requiring_environment(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["odoo-assistant-remote", "--help"])

    remote_app.main()

    assert "usage: odoo-assistant-remote" in capsys.readouterr().out


def test_no_bearer_on_mcp_is_a_401_naming_the_resource_metadata(tmp_path):
    with make_client(tmp_path) as client:
        r = client.post("/mcp", json=_rpc_body("initialize", {}),
                        headers=_AUTH_HEADERS)
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers["www-authenticate"]
    assert "oauth-protected-resource/mcp" in r.headers["www-authenticate"]


# --------------------------------------------------------------------- (3)
def test_protected_resource_metadata_names_this_server_as_its_authority(
        tmp_path):
    with make_client(tmp_path) as client:
        r = client.get("/.well-known/oauth-protected-resource/mcp")
    assert r.status_code == 200
    doc = r.json()
    assert [u.rstrip("/") for u in doc["authorization_servers"]] == [PUBLIC_URL]
    assert doc["resource"].rstrip("/") == MCP_URL
    assert doc["scopes_supported"] == ["odoo"]


# ------------------------------------------------------------------- (4)
def test_full_oauth_flow_then_initialize_and_nineteen_tools(tmp_path,
                                                            consent_connect):
    with make_client(tmp_path) as client:
        token = _full_token(client, odoo_url=ODOO_A)

        init = client.post("/mcp", headers=_AUTH_HEADERS | {
            "Authorization": f"Bearer {token}"},
            json=_rpc_body("initialize", {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"}}))
        assert init.status_code == 200, init.text
        answer = _rpc_result(init)
        assert "result" in answer
        assert answer["result"]["serverInfo"]["name"] == "odoo-assistant"

        listed = client.post("/mcp", headers=_AUTH_HEADERS | {
            "Authorization": f"Bearer {token}"},
            json=_rpc_body("tools/list", {}, id=2))
        names = {t["name"] for t in _rpc_result(listed)["result"]["tools"]}
    assert len(names) == 19
    assert "count_records" in names
    assert consent_connect.calls == [{
        "base": ODOO_A, "db": DB, "user": "", "key": API_KEY}]


# ------------------------------------------------------------------- (5)
def test_each_token_reaches_only_its_own_tenants_odoo(
        tmp_path, consent_connect, tenant_odoos):
    with make_client(tmp_path) as client:
        token_a = _full_token(client, odoo_url=ODOO_A)
        token_b = _full_token(client, odoo_url=ODOO_B)

        for token, expected in ((token_a, COUNT_A), (token_b, COUNT_B),
                                (token_a, COUNT_A)):
            answered = client.post("/mcp", headers=_AUTH_HEADERS | {
                "Authorization": f"Bearer {token}"},
                json=_rpc_body("tools/call", {
                    "name": "count_records",
                    "arguments": {"model": "res.partner"}}, id=3))
            assert answered.status_code == 200, answered.text
            payload = _rpc_result(answered)
            assert "result" in payload, payload
            assert payload["result"]["content"][0]["text"] == str(expected)

    assert [f.base_url for f in tenant_odoos.values()] == [ODOO_A, ODOO_B]
    assert tenant_odoos[ODOO_A].searches
    assert tenant_odoos[ODOO_B].searches


# ------------------------------------------------------------------- (6)
def test_a_token_whose_tenant_row_is_deleted_answers_401(tmp_path,
                                                         consent_connect):
    settings = make_settings(tmp_path)
    with TestClient(build_app(settings), base_url=PUBLIC_URL,
                    follow_redirects=False) as client:
        token = _full_token(client, odoo_url=ODOO_A)
        store = Store(settings.data_dir / "remote.db", settings.secret_key)
        tenant = store.find_tenant_by_key_hash(key_hash(ODOO_A, API_KEY))
        assert tenant is not None
        store.delete_tenant(tenant.subject)

        gone = client.post("/mcp", headers=_AUTH_HEADERS | {
            "Authorization": f"Bearer {token}"},
            json=_rpc_body("tools/list", {}, id=4))
    assert gone.status_code == 401
    assert "resource_metadata=" in gone.headers["www-authenticate"]


# ------------------------------------------------------------------- (7)
@pytest.mark.parametrize("name", [
    "ODOO_BASE_URL", "ODOO_API_KEY", "ODOO_DB", "ODOO_USER"])
def test_a_tenant_env_var_refuses_startup_naming_it(tmp_path, monkeypatch,
                                                    name):
    monkeypatch.setenv(name, "x")
    with pytest.raises(RuntimeError, match=name):
        build_app(make_settings(tmp_path))


# ------------------------------------------------------------------- (8)
def test_privacy_is_html_with_the_substituted_publisher(tmp_path):
    with make_client(tmp_path) as client:
        r = client.get("/privacy")
        landing = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "the odoo-assistant maintainers" in r.text
    assert "{{PUBLISHER}}" not in r.text and "{{SUPPORT_EMAIL}}" not in r.text
    assert landing.status_code == 200
    assert "/mcp" in landing.text and "/privacy" in landing.text


def test_the_challenge_route_echoes_the_configured_value_or_404s(tmp_path):
    with make_client(tmp_path, openai_challenge="abc") as client:
        assert client.get("/.well-known/openai-apps-challenge").text == "abc"
    with make_client(tmp_path) as client:
        r = client.get("/.well-known/openai-apps-challenge")
    assert r.status_code == 404


def test_settings_from_env_parses_the_documented_variables(tmp_path,
                                                           monkeypatch):
    monkeypatch.setenv("ODOO_MCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ODOO_REMOTE_PUBLIC_URL", "http://localhost:8111/")
    monkeypatch.setenv("ODOO_REMOTE_SECRET_KEY",
                       Fernet.generate_key().decode())
    monkeypatch.setenv("ODOO_REMOTE_OPENAI_CHALLENGE", "abc")
    monkeypatch.setenv("ODOO_REMOTE_ALLOW_PRIVATE_TARGETS", "1")
    settings = RemoteSettings.from_env()
    assert settings.public_url == "http://localhost:8111"  # no trailing slash
    assert settings.port == 8111  # the public URL's own port by default
    assert settings.host == "0.0.0.0"
    assert settings.openai_challenge == "abc"
    assert settings.data_dir == tmp_path
    assert settings.allow_private_targets is True
    monkeypatch.setenv("PORT", "9000")
    assert RemoteSettings.from_env().port == 9000  # PORT still overrides

    monkeypatch.setenv("ODOO_REMOTE_PUBLIC_URL", "http://evil.example")
    with pytest.raises(RuntimeError, match="ODOO_REMOTE_PUBLIC_URL"):
        RemoteSettings.from_env()
    monkeypatch.setenv("ODOO_REMOTE_PUBLIC_URL", "http://localhost:8111")
    monkeypatch.delenv("ODOO_REMOTE_SECRET_KEY")
    with pytest.raises(RuntimeError, match="ODOO_REMOTE_SECRET_KEY"):
        RemoteSettings.from_env()


# ------------------------------------------------- startup purges (lifespan)
def test_startup_purges_expired_files_but_keeps_live_ones(tmp_path,
                                                          consent_connect):
    """Given an expired file row and a live one, When the lifespan runs,
    Then the expired token answers 404 and the live one still serves."""
    files.configure_data_dir(tmp_path)
    payload = tmp_path / "doc.txt"
    payload.write_bytes(b"payload")
    expired = files.publish(payload, "t_seed", public_url=PUBLIC_URL,
                            ttl_minutes=0)
    payload.write_bytes(b"payload")
    fresh = files.publish(payload, "t_seed", public_url=PUBLIC_URL)

    with make_client(tmp_path) as client:  # lifespan runs the purges
        gone = client.get(f"/files/{expired['url'].rsplit('/', 1)[-1]}")
        alive = client.get(f"/files/{fresh['url'].rsplit('/', 1)[-1]}")
    assert gone.status_code == 404
    assert alive.status_code == 200
