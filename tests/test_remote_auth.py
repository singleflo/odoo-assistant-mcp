"""End-to-end drives of the SDK's own OAuth routes over OdooAssistantAuthProvider.

The app is `create_auth_routes` (plus the protected-resource metadata route) on
a plain Starlette app — no /mcp, so httpx's ASGITransport needs no lifespan —
driven through the real HTTP surface: metadata, DCR (public and confidential),
/authorize with PKCE S256, the consent hand-off, /token for both grants,
/revoke, and the adversarial cases (wrong verifier, redirect mismatch, code
replay, expired code, deleted tenant).

Needs the `remote` extra (`uv sync --extra remote`): cryptography lives there,
not in the base environment a stdio install resolves.
"""
import asyncio
import base64
import hashlib
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

pytest.importorskip(
    "cryptography.fernet",
    reason="the auth provider needs the [remote] extra: uv sync --extra remote")

# noqa: E402 - the gate above must run before these; cryptography is remote-only
from cryptography.fernet import Fernet  # noqa: E402
from mcp.server.auth.routes import (  # noqa: E402
    create_auth_routes,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import (  # noqa: E402
    ClientRegistrationOptions,
    RevocationOptions,
)
from pydantic import AnyHttpUrl  # noqa: E402
from starlette.applications import Starlette  # noqa: E402

from odoo_assistant.tenant import Tenant  # noqa: E402
from odoo_assistant.remote.auth import OdooAssistantAuthProvider  # noqa: E402
from odoo_assistant.remote.store import AuthCode, Store  # noqa: E402

PUBLIC_URL = "http://localhost:8000"
SERVER = f"{PUBLIC_URL}/mcp"
REDIRECT_URI = "http://127.0.0.1:43123/callback"
SUBJECT = "subj-1"


def _build(tmp_path):
    """Store + provider + the SDK's route builders on a plain Starlette app."""
    st = Store(tmp_path / "remote.db", Fernet.generate_key().decode())
    st.init()
    provider = OdooAssistantAuthProvider(st, PUBLIC_URL)
    routes = create_auth_routes(
        provider,
        issuer_url=AnyHttpUrl(PUBLIC_URL),
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=["odoo"], default_scopes=["odoo"]),
        revocation_options=RevocationOptions(enabled=True),
    )
    routes += create_protected_resource_routes(
        resource_url=AnyHttpUrl(SERVER),
        authorization_servers=[AnyHttpUrl(PUBLIC_URL)])
    return st, provider, Starlette(routes=routes)


@asynccontextmanager
async def _server(tmp_path):
    """One store (with a consented tenant) behind the ASGI app over httpx."""
    st, provider, app = _build(tmp_path)
    st.put_tenant(Tenant(SUBJECT, "https://odoo.example", "the-key", "", "read"))
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=PUBLIC_URL) as http:
        yield http, provider, st




def _pkce():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


async def _register(http, **overrides):
    body = {
        "redirect_uris": [REDIRECT_URI],
        "client_name": "test host",
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    } | overrides
    r = await http.post("/register", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _authorize(http, client_id, challenge, redirect_uri=REDIRECT_URI):
    return await http.get("/authorize", params={
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "st-1",
        "scope": "odoo",
        "resource": SERVER,
    }, follow_redirects=False)


async def _consent_code(provider, redirect_response):
    """complete_consent on the 302's req id; returns the minted code."""
    consent = urlparse(redirect_response.headers["location"])
    assert consent.path == "/consent"
    req = parse_qs(consent.query)["req"][0]
    final = urlparse(provider.complete_consent(req, SUBJECT))
    query = parse_qs(final.query)
    assert query["state"] == ["st-1"]  # state carried from /authorize
    return query["code"][0]


async def _code_flow(http, provider):
    """register -> authorize -> consent; returns (client, verifier, code)."""
    client = await _register(http)
    verifier, challenge = _pkce()
    r = await _authorize(http, client["client_id"], challenge)
    assert r.status_code == 302, r.text
    return client, verifier, await _consent_code(provider, r)


async def _exchange_code(http, client_id, code, verifier, redirect_uri=REDIRECT_URI):
    return await http.post("/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
    })


async def _refresh(http, client_id, refresh_token):
    return await http.post("/token", data={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    })


# ---------------------------------------------------------------- (1) metadata
def test_metadata_advertises_s256_pkce_and_a_registration_endpoint(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, _, _):
            return await http.get("/.well-known/oauth-authorization-server")

    r = asyncio.run(scenario())
    assert r.status_code == 200
    doc = r.json()
    assert doc["code_challenge_methods_supported"] == ["S256"]
    assert doc["registration_endpoint"]
    assert doc["grant_types_supported"] == ["authorization_code", "refresh_token"]


# --------------------------------------------------------------- (2) discovery
def test_register_accepts_loopback_http_public_and_confidential(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, _, _):
            return (
                await _register(http),
                await _register(http, token_endpoint_auth_method="client_secret_post"),
            )

    public, confidential = asyncio.run(scenario())
    assert public["client_id"]  # Claude Code's loopback http:43123 accepted
    assert "client_secret" not in public  # a public client gets none
    assert confidential["client_secret"]


# -------------------------------------------------------------- (3) /authorize
def test_authorize_redirects_to_consent_with_a_pending_request(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, _, _):
            client = await _register(http)
            _, challenge = _pkce()
            return await _authorize(http, client["client_id"], challenge)

    r = asyncio.run(scenario())
    assert r.status_code == 302
    consent = urlparse(r.headers["location"])
    assert consent.path == "/consent"
    assert parse_qs(consent.query)["req"]  # pending id for the consent page


# -------------------------------------------- (4) consent + authorization_code
def test_consent_then_code_exchange_returns_a_usable_pair(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, _):
            client, verifier, code = await _code_flow(http, provider)
            r = await _exchange_code(http, client["client_id"], code, verifier)
            pair = r.json()
            loaded = await provider.load_access_token(pair.get("access_token", ""))
            return pair, loaded

    pair, loaded = asyncio.run(scenario())
    assert pair["access_token"] and pair["refresh_token"]
    assert pair["expires_in"] == 3600
    assert pair["scope"] == "odoo"
    assert loaded is not None
    assert loaded.subject == SUBJECT  # the consent-assigned tenant subject


# ------------------------------------------------------- (5) refresh rotation
def test_refresh_rotates_and_the_old_refresh_dies(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, _):
            client, verifier, code = await _code_flow(http, provider)
            first = (await _exchange_code(
                http, client["client_id"], code, verifier)).json()
            rotated = await _refresh(http, client["client_id"],
                                     first["refresh_token"])
            replay = await _refresh(http, client["client_id"],
                                    first["refresh_token"])
            return first, rotated, replay

    first, rotated, replay = asyncio.run(scenario())
    assert rotated.status_code == 200
    assert rotated.json()["access_token"] != first["access_token"]
    assert rotated.json()["refresh_token"] != first["refresh_token"]
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"


# ------------------------------------- (6) revoke access -> family + tenant die
def test_revoking_the_access_token_kills_the_family_and_the_tenant(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, st):
            client, verifier, code = await _code_flow(http, provider)
            pair = (await _exchange_code(
                http, client["client_id"], code, verifier)).json()
            revoked = await http.post("/revoke", data={
                "token": pair["access_token"], "client_id": client["client_id"],
                # the SDK's revocation model requires the key even for a
                # public client, which presents it empty
                "client_secret": ""})
            refresh_after = await _refresh(http, client["client_id"],
                                           pair["refresh_token"])
            return revoked, refresh_after, st.get_tenant(SUBJECT)

    revoked, refresh_after, tenant = asyncio.run(scenario())
    assert revoked.status_code == 200
    assert refresh_after.status_code == 400
    assert refresh_after.json()["error"] == "invalid_grant"
    assert tenant is None  # no other family alive -> the stored key is erased


# ------------------------------------------------------ (7) tenant row deleted
def test_a_deleted_tenant_makes_its_live_access_token_load_none(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, st):
            client, verifier, code = await _code_flow(http, provider)
            pair = (await _exchange_code(
                http, client["client_id"], code, verifier)).json()
            before = await provider.load_access_token(pair["access_token"])
            st.delete_tenant(SUBJECT)
            after = await provider.load_access_token(pair["access_token"])
            return before, after

    before, after = asyncio.run(scenario())
    assert before is not None
    assert after is None  # BearerAuthBackend yields no user -> 401 upstream


# ------------------------------------------------------------- (8) code replay
def test_a_second_exchange_of_the_same_code_is_invalid_grant(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, _):
            client, verifier, code = await _code_flow(http, provider)
            first = await _exchange_code(http, client["client_id"], code, verifier)
            second = await _exchange_code(http, client["client_id"], code, verifier)
            return first, second

    first, second = asyncio.run(scenario())
    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"


# ----------------------------------------------------- adversarial: bad PKCE
def test_a_wrong_code_verifier_is_invalid_grant(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, _):
            client, verifier, code = await _code_flow(http, provider)
            return await _exchange_code(
                http, client["client_id"], code, verifier + "x")

    r = asyncio.run(scenario())
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


# ------------------------------------------- adversarial: redirect_uri mismatch
def test_a_redirect_uri_mismatch_at_token_is_rejected(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, _):
            client, verifier, code = await _code_flow(http, provider)
            return await _exchange_code(
                http, client["client_id"], code, verifier,
                redirect_uri="http://127.0.0.1:49999/callback")

    r = asyncio.run(scenario())
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_request"


# --------------------------------------------------- adversarial: expired code
def test_an_expired_code_is_invalid_grant(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, st):
            client = await _register(http)
            verifier, challenge = _pkce()
            st.put_code(AuthCode(
                code="expired-code", client_id=client["client_id"],
                subject=SUBJECT, scopes="odoo", code_challenge=challenge,
                redirect_uri=REDIRECT_URI, redirect_uri_explicit=True,
                resource=None,
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
            return await _exchange_code(
                http, client["client_id"], "expired-code", verifier)

    r = asyncio.run(scenario())
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


# ------------------------------------------- adversarial: consent hand-off edge
def test_complete_consent_refuses_an_unknown_request(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, _):
            return provider.complete_consent("no-such-req", SUBJECT)

    with pytest.raises(ValueError):
        asyncio.run(scenario())


# ------------------------------------------------------------ protected resource
def test_protected_resource_metadata_names_the_authorization_server(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, _, _):
            return await http.get("/.well-known/oauth-protected-resource/mcp")

    r = asyncio.run(scenario())
    assert r.status_code == 200
    doc = r.json()
    assert doc["resource"] == SERVER  # the explicit /mcp path is kept as is
    assert doc["authorization_servers"] == [f"{PUBLIC_URL}/"]
