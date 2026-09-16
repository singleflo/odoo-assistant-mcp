"""The consent page: form, SSRF guard, verification, tenant reuse.

The page is public and unauthenticated by SDK design, so the tests assert the
things that make that safe, not just a 200: the private-address refusal
happens BEFORE any connection attempt, the API key is never echoed back (not
even escaped), the Odoo error text lands in the page html-escaped (it is
untrusted external text in a browser), an oversize body dies at 413 before
any parsing, and a re-consent reuses the tenant instead of minting subjects.

`consent._verify_isolated` and `consent.socket.getaddrinfo` are the two
seams: both are patched through the consent module's own namespace. The
verifier runs in a spawn child that re-imports the module fresh, so the
flow tests pin the PARENT-side seam — the credentials consent hands it —
not `connect` itself; the child's own behaviour is exercised by the
termination test, which swaps the child entry for a hanging one.

Needs the `remote` extra (`uv sync --extra remote`): cryptography lives
there, not in the base environment a stdio install resolves.
"""
import socket
import sqlite3
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

pytest.importorskip(
    "cryptography.fernet",
    reason="the store needs the [remote] extra: uv sync --extra remote")

# noqa: E402 - the gate above must run first, cryptography is remote-only
from cryptography.fernet import Fernet  # noqa: E402
from mcp.shared.auth import OAuthClientInformationFull  # noqa: E402
from pydantic import AnyUrl  # noqa: E402

from odoo_assistant.remote import consent  # noqa: E402
from odoo_assistant.remote.consent import ConsentDeps  # noqa: E402
from odoo_assistant.remote.store import (  # noqa: E402
    PENDING_TTL,
    PendingAuthz,
    Store,
    key_hash,
)

PUBLIC_URL = "https://mcp.example.test"
REDIRECT_URI = "http://127.0.0.1:43123/callback"
ODOO_URL = "https://acme.odoo.com"
API_KEY = "the-key-the-user-typed"
DB = "acme-prod-1234567"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FakeProvider:
    """The two methods consent calls on todo 6's provider: consume and answer
    with the final redirect URL of the client that asked for consent —
    carrying a code when the user approved, `access_denied` when refused."""

    def __init__(self):
        self.calls = []
        self.refusals = []

    def complete_consent(self, pending_id: str, subject: str) -> str:
        self.calls.append((pending_id, subject))
        return f"{REDIRECT_URI}?code=c-abc123&state=st-1"

    def refuse_consent(self, pending_id: str) -> str:
        self.refusals.append(pending_id)
        return f"{REDIRECT_URI}?error=access_denied&state=st-1"


class FakeConnect:
    """consent._verify_isolated: records the credentials consent hands over
    (the same shape connect would receive) and answers from a programmed
    error."""

    def __init__(self):
        self.error = None
        self.calls = []

    def __call__(self, odoo_url, db, api_key):
        self.calls.append({
            "base": odoo_url, "db": db, "user": "", "key": api_key})
        if self.error is not None:
            return consent._Verified("error", repr(self.error), None)
        return consent._Verified("ok", "", None)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "remote.db"


@pytest.fixture
def store(db_path):
    s = Store(db_path, Fernet.generate_key().decode())
    s.init()
    s.put_pending(_pending_row())  # one live authorisation waiting for consent
    return s


@pytest.fixture
def fake_connect(monkeypatch):
    recorder = FakeConnect()
    monkeypatch.setattr(consent, "_verify_isolated", recorder)
    return recorder


@pytest.fixture
def provider():
    return FakeProvider()


def _pending_row(**kw) -> PendingAuthz:
    row = PendingAuthz(
        id="pend-1", client_id="cid-1", redirect_uri=REDIRECT_URI,
        redirect_uri_explicit=True, scopes="odoo", code_challenge="challenge-x",
        resource=None, state="st-1", expires_at=_now() + PENDING_TTL)
    return replace(row, **kw)


def _form(**over):
    data = {"req": "pend-1", "odoo_url": ODOO_URL, "api_key": API_KEY,
            "db": DB, "policy": "read"}
    data.update(over)
    return {k: v for k, v in data.items() if v is not None}


def client(store, provider, *, allow_private_targets=False) -> TestClient:
    """A bare Starlette app with only the two consent routes (todo 8 mounts
    them). The SDK's session-manager lifespan is not involved, so a plain
    TestClient without entering it as a context manager works."""
    app = Starlette(routes=[
        Route("/consent", consent.consent_form, methods=["GET"]),
        Route("/consent", consent.consent_submit, methods=["POST"]),
    ])
    app.state.consent_deps = ConsentDeps(
        store=store, provider=provider, public_url=PUBLIC_URL,
        allow_private_targets=allow_private_targets)
    # Raw answers only: the redirect target belongs to the client, not to
    # this bare app, so following it would just manufacture a 404.
    return TestClient(app, follow_redirects=False)


def _fake_resolver(monkeypatch, address):
    def fake_getaddrinfo(host, port=None, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, 0))]
    monkeypatch.setattr(
        "odoo_assistant.remote.consent.socket.getaddrinfo", fake_getaddrinfo)


# ---------------------------------------------------------------- happy path
def test_happy_path_stores_an_encrypted_tenant_and_redirects_with_a_code(
        db_path, store, fake_connect, provider):
    """Given a pending authorisation, When the user GETs the form then POSTs
    their Odoo URL and key, Then the redirect carries a code, the provider
    got the pending id and one t_ subject, and the raw database file holds no
    plaintext key."""
    c = client(store, provider)

    shown = c.get("/consent?req=pend-1")

    assert shown.status_code == 200
    assert 'name="req" value="pend-1"' in shown.text
    assert 'value="read" checked' in shown.text
    assert f'href="{PUBLIC_URL}/privacy"' in shown.text
    assert "Read only: the assistant can look, never change" in shown.text
    assert ("Standard: create, update, confirm; never delete, cancel or"
            " mass-mail") in shown.text

    answer = c.post("/consent", data=_form())

    assert answer.status_code == 302
    assert answer.headers["location"].startswith(REDIRECT_URI)
    assert "code=" in answer.headers["location"]
    assert len(provider.calls) == 1
    assert provider.calls[0][0] == "pend-1"
    subject = provider.calls[0][1]
    assert subject.startswith("t_")
    assert fake_connect.calls == [{
        "base": ODOO_URL, "db": DB, "user": "", "key": API_KEY}]
    tenant = store.find_tenant_by_key_hash(key_hash(ODOO_URL, API_KEY))
    assert tenant is not None and tenant.subject == subject
    assert store.get_tenant(subject).api_key == API_KEY
    blob = db_path.read_bytes()
    wal = db_path.with_name("remote.db-wal")
    if wal.exists():
        blob += wal.read_bytes()
    assert API_KEY.encode() not in blob


def test_the_form_names_who_is_asking_and_where_the_access_returns(
        store, provider):
    """Given a client that registered a name, When the form renders, Then it
    names that client, the redirect its access returns to and the scope.

    The MCP specification's consent-UI rules require all three: a page that
    hides them asks the user to approve an unnamed stranger. The registered
    name is attacker-supplied text, so the second half of this test is that
    it arrives escaped rather than as markup.
    """
    store.put_client(OAuthClientInformationFull(
        client_id="cid-1", client_name="Claude <Desktop>",
        redirect_uris=[AnyUrl(REDIRECT_URI)],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"], scope="odoo",
        token_endpoint_auth_method="none"))

    shown = client(store, provider).get("/consent?req=pend-1")

    assert shown.status_code == 200
    assert "Claude &lt;Desktop&gt;" in shown.text
    assert "<Desktop>" not in shown.text
    assert REDIRECT_URI in shown.text
    assert "odoo" in shown.text


def test_the_form_carries_the_waiting_message_it_will_reveal(store, provider):
    """Pressing Connect waits on a live connection to someone's Odoo, up to
    `_VERIFY_TIMEOUT`. The note that explains the wait ships with the page,
    hidden, so revealing it costs no request — and the whole affordance
    degrades to a plain submit where scripts do not run."""
    shown = client(store, provider).get("/consent?req=pend-1")

    assert 'class="sending-note"' in shown.text
    assert 'role="status"' in shown.text
    assert "up to twenty seconds" in shown.text


def test_an_unregistered_client_is_named_by_its_id_rather_than_left_blank(
        store, provider):
    """Given no registration record for the pending client, When the form
    renders, Then the client id stands in — the page never says "something"
    is asking."""
    shown = client(store, provider).get("/consent?req=pend-1")

    assert "cid-1" in shown.text


def test_refusing_answers_access_denied_and_touches_nothing(
        db_path, store, provider, fake_connect):
    """Given the form open, When the user presses Refuse, Then the browser is
    sent back to the client with `access_denied`, no Odoo is contacted and no
    tenant is stored.

    Closing the window would leave the client waiting on a flow that never
    ends; OAuth gives refusal its own answer, and this is it. The refusal
    reaches the provider, which is where the pending row is consumed — see
    `test_remote_auth.py` for that half.
    """
    c = client(store, provider)

    answer = c.post("/consent", data={"req": "pend-1", "action": "deny"})

    assert answer.status_code == 302
    assert "error=access_denied" in answer.headers["location"]
    assert provider.refusals == ["pend-1"]
    assert provider.calls == []
    assert fake_connect.calls == []
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT count(*) FROM tenants").fetchone()[0] == 0


def test_a_second_consent_with_the_same_url_and_key_reuses_the_subject(
        db_path, store, fake_connect, provider):
    """Given a tenant already stored for this URL+key, When the user
    reconnects through a new pending request and picks another policy,
    Then the SAME subject is reused with the new policy — one tenant row,
    no subject proliferation."""
    c = client(store, provider)
    c.post("/consent", data=_form())
    first_subject = provider.calls[0][1]
    store.put_pending(_pending_row(id="pend-2"))

    answer = c.post("/consent", data=_form(req="pend-2", policy="standard"))

    assert answer.status_code == 302
    assert provider.calls[1][1] == first_subject
    tenant = store.get_tenant(first_subject)
    assert tenant.policy == "standard"
    assert tenant.db == DB
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT count(*) FROM tenants").fetchone()[0] == 1


# -------------------------------------------------------------------- errors
def test_a_wrong_key_re_renders_the_error_and_stores_nothing(
        store, fake_connect, provider):
    """Given a key Odoo refuses, When consent is submitted, Then the page
    re-renders with Odoo's error, nothing is stored, no code is minted — and
    the pending request survives, so a corrected key completes the flow."""
    fake_connect.error = ValueError("Access Denied: the key is not valid")
    c = client(store, provider)

    answer = c.post("/consent", data=_form())

    assert answer.status_code == 200
    assert "Access Denied" in answer.text
    assert store.find_tenant_by_key_hash(key_hash(ODOO_URL, API_KEY)) is None
    assert provider.calls == []
    assert len(fake_connect.calls) == 1  # the verification DID reach Odoo

    fake_connect.error = None
    retry = c.post("/consent", data=_form())
    assert retry.status_code == 302


def test_the_odoo_error_text_is_rendered_escaped(store, fake_connect,
                                                 provider):
    """Given an Odoo error carrying markup, When it is re-rendered,
    Then the page shows it escaped — an untrusted external string never
    becomes html in a browser page."""
    fake_connect.error = ValueError("<script>alert('xss')</script>")
    c = client(store, provider)

    answer = c.post("/consent", data=_form())

    assert answer.status_code == 200
    assert "&lt;script&gt;" in answer.text
    assert "alert('xss')" not in answer.text
    # The page does carry one script of its own — the submit-state helper the
    # policy admits by hash — so counting tags is what separates "ours" from
    # "the error text became markup", where a bare `"<script>" not in` no
    # longer can.
    assert answer.text.count("<script") == 1
    assert "dataset.sending" in answer.text


def test_a_failed_attempt_never_echoes_the_api_key(store, fake_connect,
                                                   provider):
    """Given a failed verification, When the form re-renders,
    Then the submitted key appears nowhere in the page — the URL is
    prefilled, the password field is not."""
    fake_connect.error = ValueError("Access Denied")
    c = client(store, provider)

    answer = c.post("/consent", data=_form())

    assert answer.status_code == 200
    assert API_KEY not in answer.text
    assert ODOO_URL in answer.text
    assert 'type="password"' in answer.text


def test_missing_fields_re_render_the_form(store, fake_connect, provider):
    """Given a submission without URL or key, When it is posted,
    Then the page re-renders with the request to fill them in and Odoo is
    never dialed."""
    c = client(store, provider)

    answer = c.post("/consent", data=_form(odoo_url="", api_key=""))

    assert answer.status_code == 200
    assert "Fill in the Odoo URL and the API key" in answer.text
    assert fake_connect.calls == []
    assert provider.calls == []


def test_plain_http_to_a_remote_host_is_rejected(store, fake_connect,
                                                 provider):
    """Given an http:// URL to a remote host, When submitted,
    Then the form re-renders asking for https and nothing is dialed."""
    c = client(store, provider)

    answer = c.post("/consent", data=_form(odoo_url="http://acme.odoo.com"))

    assert answer.status_code == 200
    assert "https" in answer.text
    assert fake_connect.calls == []
    assert provider.calls == []


# --------------------------------------------------------------- SSRF guard
@pytest.mark.parametrize(
    "address", ["169.254.169.254", "10.0.0.5", "100.64.0.1"])
def test_private_addresses_are_refused_before_any_connection(
        store, fake_connect, provider, monkeypatch, address):
    """Given an URL that resolves into a refused range, When submitted,
    Then the page answers 'public host' and connect is NEVER invoked — the
    unauthenticated endpoint must not become an internal prober."""
    _fake_resolver(monkeypatch, address)
    c = client(store, provider)

    answer = c.post("/consent", data=_form())

    assert answer.status_code == 200
    assert "public host" in answer.text
    assert fake_connect.calls == []
    assert provider.calls == []


def test_allow_private_targets_admits_the_private_address(
        store, fake_connect, provider, monkeypatch):
    """Given the dev override on and a private target, When submitted,
    Then the flow proceeds all the way to the redirect."""
    _fake_resolver(monkeypatch, "169.254.169.254")
    c = client(store, provider, allow_private_targets=True)

    answer = c.post(
        "/consent", data=_form(odoo_url="https://169.254.169.254"))

    assert answer.status_code == 302
    assert fake_connect.calls[0]["base"] == "https://169.254.169.254"


def test_localhost_http_is_accepted_for_a_server_on_this_machine(
        store, fake_connect, provider, monkeypatch):
    """Given the dev override and http://localhost, When submitted,
    Then the https rule admits it — the documented same-box dev path."""
    _fake_resolver(monkeypatch, "127.0.0.1")
    c = client(store, provider, allow_private_targets=True)

    answer = c.post("/consent", data=_form(odoo_url="http://localhost:8069"))

    assert answer.status_code == 302
    assert fake_connect.calls[0]["base"] == "http://localhost:8069"


def _hang_entry(odoo_url: str, db: str, api_key: str, send_conn) -> None:
    """Spawn-child target for the termination test: a verifier that never
    answers — the shape of an Odoo host that accepts the socket and stalls."""
    time.sleep(30)


def test_a_hanging_verification_is_terminated_and_the_request_answers(
        store, provider, monkeypatch):
    """Given a verifier child that never answers, When the deadline passes,
    Then the REQUEST answers the timeout page promptly AND no process
    survives the helper — the cancelled-thread version left a worker blocked
    on the socket forever."""
    monkeypatch.setattr(consent, "_VERIFY_TIMEOUT", 0.2)
    monkeypatch.setattr(consent, "_verify_entry", _hang_entry)
    c = client(store, provider, allow_private_targets=True)

    before = time.monotonic()
    answer = c.post("/consent", data=_form())
    elapsed = time.monotonic() - before

    assert answer.status_code == 200
    assert "did not answer" in answer.text
    assert elapsed < 8  # the child hangs for 30 s; answering proves the reap
    verifier = consent._last_verifier
    assert verifier is not None
    assert not verifier.is_alive()
    assert verifier.exitcode is not None


# --------------------------------------------------------------- body & req
def test_an_oversized_body_answers_413(store, fake_connect, provider):
    """Given a 65 KiB body, When posted, Then 413 — custom routes sit
    outside the SDK's 4 MiB limit, so consent caps itself before parsing."""
    c = client(store, provider)
    big = b"odoo_url=x&filler=" + b"q" * (65 * 1024)

    answer = c.post(
        "/consent", content=big,
        headers={"content-type": "application/x-www-form-urlencoded"})

    assert answer.status_code == 413
    assert fake_connect.calls == []
    assert provider.calls == []


def test_an_expired_or_unknown_req_answers_400(store, fake_connect,
                                               provider):
    """Given a pending row already expired (or an id never issued), When the
    page is opened or the form posted, Then 400 — and Odoo is never dialed."""
    store.put_pending(_pending_row(expires_at=_now() - timedelta(minutes=1)))
    c = client(store, provider)

    assert c.get("/consent?req=pend-1").status_code == 400
    assert c.post("/consent", data=_form()).status_code == 400
    assert c.get("/consent?req=never-issued").status_code == 400
    assert fake_connect.calls == []
    assert provider.calls == []
