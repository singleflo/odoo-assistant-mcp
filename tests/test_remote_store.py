"""Round trips and secrecy guarantees for the remote server's SQLite store.

Every table gets a write-read cycle in a tmp_path database; the retention
contract's adversarial cases (expiry, single use, family revocation, idle
purge, a fresh Store over a WAL database) get one each; and the raw file is
scanned to prove no plaintext key or raw token ever reaches the disk.

Needs the `remote` extra (`uv sync --extra remote`): cryptography lives there,
not in the base environment a stdio install resolves.
"""
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest

pytest.importorskip(
    "cryptography.fernet",
    reason="the store needs the [remote] extra: uv sync --extra remote")

# noqa: E402 - the gate above must run before these, cryptography is remote-only
from cryptography.fernet import Fernet, InvalidToken  # noqa: E402
from mcp.shared.auth import OAuthClientInformationFull  # noqa: E402

from odoo_assistant.tenant import Tenant  # noqa: E402
from odoo_assistant.remote.store import (  # noqa: E402
    ACCESS_TTL,
    CODE_TTL,
    PENDING_TTL,
    REFRESH_TTL,
    SECRET_KEY_MESSAGE,
    AccessToken,
    AuthCode,
    PendingAuthz,
    RefreshToken,
    Store,
    hash_token,
    key_hash,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "remote.db"


@pytest.fixture
def secret() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def st(db_path, secret):
    s = Store(db_path, secret)
    s.init()
    return s


def _client(client_id="cid-1", **overrides):
    return OAuthClientInformationFull.model_validate({
        "client_id": client_id,
        "redirect_uris": ["http://localhost:9000/callback"]} | overrides)


def _pending(**kw) -> PendingAuthz:
    row = PendingAuthz(
        id="pend-1", client_id="cid-1", redirect_uri="http://localhost:9000/callback",
        redirect_uri_explicit=True, scopes="odoo", code_challenge="challenge-x",
        resource="http://localhost:8000/mcp", state="st-1", expires_at=_now() + PENDING_TTL)
    return replace(row, **kw)


def _code(**kw) -> AuthCode:
    row = AuthCode(
        code="code-1", client_id="cid-1", subject="subj-1", scopes="odoo",
        code_challenge="challenge-x", redirect_uri="http://localhost:9000/callback",
        redirect_uri_explicit=True, resource=None, expires_at=_now() + CODE_TTL)
    return replace(row, **kw)


def _access(**kw) -> AccessToken:
    row = AccessToken(
        token_hash=hash_token("access-raw"), family_id="fam-1", client_id="cid-1",
        subject="subj-1", scopes="odoo", expires_at=_now() + ACCESS_TTL)
    return replace(row, **kw)


def _refresh(**kw) -> RefreshToken:
    row = RefreshToken(
        token_hash=hash_token("refresh-raw"), family_id="fam-1", client_id="cid-1",
        subject="subj-1", scopes="odoo", expires_at=_now() + REFRESH_TTL)
    return replace(row, **kw)


def _tenant(subject="subj-1"):
    return Tenant(subject, "https://odoo.example", "the-key", "", "read")


def _backdate(db_path, subject, days):
    """Push last_used_at `days` into the past, the only way to age a tenant."""
    old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with sqlite3.connect(db_path) as db:
        db.execute(
            "UPDATE tenants SET last_used_at = ? WHERE subject = ?", (old, subject))


def _raw_db_bytes(db_path) -> bytes:
    """Main file plus the WAL, so a scan cannot miss recently written pages."""
    blob = db_path.read_bytes()
    wal = db_path.with_name(db_path.name + "-wal")
    if wal.exists():
        blob += wal.read_bytes()
    return blob


# ---------------------------------------------------------------- TTL policy
def test_ttl_constants_carry_the_promised_retention():
    assert ACCESS_TTL == timedelta(hours=1)
    assert REFRESH_TTL == timedelta(days=30)
    assert CODE_TTL == timedelta(minutes=10)
    assert PENDING_TTL == timedelta(minutes=10)


def test_hash_helpers_are_plain_sha256_hex():
    assert hash_token("raw") == hashlib.sha256(b"raw").hexdigest()
    assert key_hash("https://odoo.example", "k") == hashlib.sha256(
        b"https://odoo.example\nk").hexdigest()


# --------------------------------------------------------- OAuth clients
def test_client_round_trip(st):
    st.put_client(_client())
    assert st.get_client("cid-1") == _client()
    assert st.get_client("unknown") is None


# --------------------------------------------------- pending authorisations
def test_pending_pop_deletes_on_read(st):
    row = _pending()
    st.put_pending(row)
    assert st.pop_pending("pend-1") == row
    assert st.pop_pending("pend-1") is None


@pytest.mark.parametrize("force_fallback", [False, True])
def test_pending_pop_has_exactly_one_winner_under_concurrency(
        st, monkeypatch, force_fallback):
    if force_fallback:
        monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 34, 0))
    st.put_pending(_pending())
    barrier = Barrier(20)

    def race():
        barrier.wait()
        return st.pop_pending("pend-1")

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: race(), range(20)))

    assert sum(row is not None for row in results) == 1


def test_pending_expired_is_refused_and_dropped(st):
    st.put_pending(_pending(expires_at=_now() - timedelta(seconds=1)))
    assert st.pop_pending("pend-1") is None
    assert st.pop_pending("pend-1") is None  # the read deleted it


# --------------------------------------------------------------- auth codes
def test_code_loads_then_consumes_exactly_once(st):
    row = _code()
    st.put_code(row)
    assert st.load_code("code-1") == row
    assert st.consume_code("code-1") == row
    assert st.consume_code("code-1") is None  # single use
    assert st.load_code("code-1") is None  # used reads as absent


@pytest.mark.parametrize("force_fallback", [False, True])
def test_code_consume_has_exactly_one_winner_under_concurrency(
        st, monkeypatch, force_fallback):
    if force_fallback:
        monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 34, 0))
    st.put_code(_code())
    barrier = Barrier(20)

    def race():
        barrier.wait()
        return st.consume_code("code-1")

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: race(), range(20)))

    assert sum(row is not None for row in results) == 1


def test_code_expired_is_refused(st):
    st.put_code(_code(expires_at=_now() - timedelta(seconds=1)))
    assert st.load_code("code-1") is None
    assert st.consume_code("code-1") is None


# ------------------------------------------------------------------- tokens
def test_access_round_trip_and_unknown_hash(st):
    row = _access()
    st.put_access(row)
    assert st.load_access(hash_token("access-raw")) == row
    assert st.load_access("never-minted") is None


def test_expired_access_loads_as_none_and_is_deleted(st, db_path):
    st.put_access(_access(expires_at=_now() - timedelta(seconds=1)))
    assert st.load_access(hash_token("access-raw")) is None
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT count(*) FROM access_tokens").fetchone()[0] == 0


def test_refresh_round_trip_and_unknown_hash(st):
    row = _refresh()
    st.put_refresh(row)
    assert st.load_refresh(hash_token("refresh-raw")) == row
    assert st.load_refresh("never-minted") is None


def test_revoke_family_unloads_both_tokens(st):
    st.put_access(_access())
    st.put_refresh(_refresh())
    st.revoke_family("fam-1")
    assert st.load_access(hash_token("access-raw")) is None
    assert st.load_refresh(hash_token("refresh-raw")) is None


def test_code_exchange_rolls_back_consumption_when_pair_insert_fails(
        st, monkeypatch):
    code = _code()
    st.put_code(code)

    def fail_pair(*args):
        raise RuntimeError("injected pair failure")

    monkeypatch.setattr(Store, "_put_pair", staticmethod(fail_pair))

    with pytest.raises(RuntimeError, match="injected pair failure"):
        st.exchange_code_pair("code-1", _access(), _refresh())

    assert st.load_code("code-1") == code


def test_refresh_rotation_rolls_back_revocation_when_pair_insert_fails(
        st, monkeypatch):
    access = _access()
    refresh = _refresh()
    st.put_access(access)
    st.put_refresh(refresh)

    def fail_pair(*args):
        raise RuntimeError("injected pair failure")

    monkeypatch.setattr(Store, "_put_pair", staticmethod(fail_pair))

    with pytest.raises(RuntimeError, match="injected pair failure"):
        st.rotate_refresh_pair(
            hash_token("refresh-raw"), _access(), _refresh())

    assert st.load_access(hash_token("access-raw")) == access
    assert st.load_refresh(hash_token("refresh-raw")) == refresh


def test_tokens_alive_tracks_the_family_lifecycle(st):
    assert st.tokens_alive("subj-1") is False
    st.put_access(_access())
    st.put_refresh(_refresh())
    assert st.tokens_alive("subj-1") is True
    st.revoke_family("fam-1")
    assert st.tokens_alive("subj-1") is False


# ------------------------------------------------------------------ tenants
def test_tenant_round_trip_decrypts_the_key(st):
    st.put_tenant(_tenant())
    assert st.get_tenant("subj-1") == _tenant()
    assert st.get_tenant("unknown") is None


def test_find_tenant_by_key_hash(st):
    st.put_tenant(_tenant())
    assert st.find_tenant_by_key_hash(
        key_hash("https://odoo.example", "the-key")) == _tenant()
    assert st.find_tenant_by_key_hash("no-such-hash") is None


def test_delete_tenant_is_idempotent(st):
    st.put_tenant(_tenant())
    st.delete_tenant("subj-1")
    st.delete_tenant("subj-1")
    assert st.get_tenant("subj-1") is None


def test_touch_refreshes_last_used_so_idle_purge_keeps_the_tenant(st, db_path):
    st.put_tenant(_tenant())
    _backdate(db_path, "subj-1", days=91)
    st.purge_idle_tenants(days=90)
    assert st.get_tenant("subj-1") is None  # control: idle and untouched -> gone

    st.put_tenant(_tenant())
    _backdate(db_path, "subj-1", days=91)
    st.touch_tenant("subj-1")  # a use inside the window restarts the clock
    st.purge_idle_tenants(days=90)
    assert st.get_tenant("subj-1") == _tenant()


def test_purge_idle_removes_only_tokenless_idle_tenants(st, db_path):
    st.put_tenant(_tenant("gone"))  # idle 91 days, no live token
    st.put_tenant(_tenant("kept"))  # idle 91 days, live refresh token
    st.put_refresh(_refresh(subject="kept", token_hash=hash_token("kept-refresh")))
    st.put_tenant(_tenant("fresh"))  # recently used
    _backdate(db_path, "gone", days=91)
    _backdate(db_path, "kept", days=91)
    st.purge_idle_tenants(days=90)
    assert st.get_tenant("gone") is None
    assert st.get_tenant("kept") == _tenant("kept")
    assert st.get_tenant("fresh") == _tenant("fresh")


# --------------------------------------------------------------- purge + WAL
def test_purge_expired_cleans_and_a_new_store_agrees(st, db_path, secret):
    st.put_pending(_pending(expires_at=_now() - timedelta(seconds=1)))
    st.put_code(_code(expires_at=_now() - timedelta(seconds=1)))
    st.put_access(_access(expires_at=_now() - timedelta(seconds=1)))
    st.put_refresh(_refresh(expires_at=_now() - timedelta(seconds=1)))
    st.put_refresh(_refresh(token_hash=hash_token("revoked-refresh"), family_id="fam-dead"))
    st.revoke_family("fam-dead")
    st.put_refresh(_refresh(token_hash=hash_token("live-refresh"), family_id="fam-live"))
    st.purge_expired()

    fresh = Store(db_path, secret)  # a NEW instance reads the same committed state
    assert fresh.pop_pending("pend-1") is None
    assert fresh.consume_code("code-1") is None
    assert fresh.load_access(hash_token("access-raw")) is None
    assert fresh.load_refresh(hash_token("refresh-raw")) is None  # expired
    assert fresh.load_refresh(hash_token("revoked-refresh")) is None  # revoked
    assert fresh.load_refresh(hash_token("live-refresh")) is not None


# ------------------------------------------------------------------ secrecy
def test_db_file_carries_no_plaintext_secret(st, db_path):
    client_secret = "dcr-client-secret-must-be-sealed"
    st.put_client(_client(
        token_endpoint_auth_method="client_secret_post",
        client_secret=client_secret))
    st.put_tenant(_tenant())
    st.put_code(_code(code="authorization-code-must-be-hashed"))
    st.put_access(_access())
    st.put_refresh(_refresh())
    blob = _raw_db_bytes(db_path)
    assert client_secret.encode() not in blob
    assert b"the-key" not in blob
    assert b"authorization-code-must-be-hashed" not in blob
    assert b"access-raw" not in blob
    assert b"refresh-raw" not in blob


def test_wrong_fernet_key_cannot_decrypt(tmp_path):
    first = Store(tmp_path / "remote.db", Fernet.generate_key().decode())
    first.init()
    first.put_tenant(_tenant())
    second = Store(tmp_path / "remote.db", Fernet.generate_key().decode())
    second.init()
    with pytest.raises(InvalidToken):
        second.get_tenant("subj-1")


def test_non_fernet_secret_names_the_generation_command(tmp_path):
    with pytest.raises(ValueError) as caught:
        Store(tmp_path / "remote.db", "hunter2")
    assert caught.value.args[0] == SECRET_KEY_MESSAGE
