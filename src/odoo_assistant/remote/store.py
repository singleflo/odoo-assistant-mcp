"""SQLite persistence for the hosted server: clients, authorisations, tokens, tenants.

Durable state for `odoo-assistant-remote` only — the stdio server never touches
it. Two security contracts shape every table:

* **Tokens are stored hashed.** The caller (the auth provider) mints every
  pending id, auth code, access token and refresh token with
  `secrets.token_urlsafe(24)` — 192 bits; RFC 6749 §10.10 wants at least 128 —
  and hands the Store raw pending ids and codes only for in-memory lookup,
  while their database keys and every access/refresh token use sha256 hex
  (`hash_token`). Raw token material never reaches the disk.
* **The Odoo API key is stored encrypted.** `put_tenant` seals it with Fernet
  under the process secret; `get_tenant` opens it. The secret must be a
  32-byte urlsafe-base64 key — what `Fernet.generate_key()` prints — and is
  never derived from a passphrase.

TTLs are the retention the privacy page promises; whoever mints a row sets its
`expires_at` from them. Timestamps are timezone-aware UTC datetimes stored as
ISO-8601 strings, which sort correctly, so expiry comparisons happen in SQL.
Connections open per call (`check_same_thread=False`, WAL) because tool bodies
run on worker threads.
"""
import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from cryptography.fernet import Fernet
from mcp.shared.auth import OAuthClientInformationFull

from odoo_assistant import tenant as tenant_context
from odoo_assistant.tenant import Tenant

SECRET_KEY_MESSAGE = (
    "ODOO_REMOTE_SECRET_KEY must be a Fernet key; generate one with: "
    "python -c 'from cryptography.fernet import Fernet;"
    "print(Fernet.generate_key().decode())'"
)

ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
CODE_TTL = timedelta(minutes=10)
PENDING_TTL = timedelta(minutes=10)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id TEXT PRIMARY KEY, client_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pending_authz (
    id TEXT PRIMARY KEY, client_id TEXT NOT NULL, redirect_uri TEXT NOT NULL,
    redirect_uri_explicit INT NOT NULL, scopes TEXT, code_challenge TEXT,
    resource TEXT, state TEXT, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS auth_codes (
    code TEXT PRIMARY KEY, client_id TEXT NOT NULL, subject TEXT NOT NULL,
    scopes TEXT, code_challenge TEXT, redirect_uri TEXT,
    redirect_uri_explicit INT NOT NULL, resource TEXT,
    expires_at TEXT NOT NULL, used INT NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS access_tokens (
    token_hash TEXT PRIMARY KEY, family_id TEXT NOT NULL, client_id TEXT NOT NULL,
    subject TEXT NOT NULL, scopes TEXT, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash TEXT PRIMARY KEY, family_id TEXT NOT NULL, client_id TEXT NOT NULL,
    subject TEXT NOT NULL, scopes TEXT, expires_at TEXT NOT NULL,
    revoked INT NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS tenants (
    subject TEXT PRIMARY KEY, base_url TEXT NOT NULL, key_hash TEXT NOT NULL,
    api_key_enc BLOB NOT NULL, db TEXT, policy TEXT NOT NULL,
    created_at TEXT NOT NULL, last_used_at TEXT NOT NULL, login TEXT);
"""

# Columns added after the first release. `CREATE TABLE IF NOT EXISTS` leaves an
# existing table exactly as it was, so a database written before the column
# existed keeps its old shape and every read of the new name fails — on the
# deployed instance, not here. `init()` adds what is missing, and SQLite's
# ADD COLUMN is an O(1) catalogue change that fills the rows with NULL.
_ADDED_COLUMNS = (("tenants", "login", "TEXT"),)


def hash_token(raw: str) -> str:
    """sha256 hex — the only form of a token that touches the disk."""
    return hashlib.sha256(raw.encode()).hexdigest()


def key_hash(base_url: str, api_key: str) -> str:
    """Identifies one Odoo connection, so a re-consent reuses its tenant."""
    return hashlib.sha256((base_url + "\n" + api_key).encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass(frozen=True, slots=True)
class PendingAuthz:
    """One consent waiting for the user; `id` is caller-minted entropy."""

    id: str
    client_id: str
    redirect_uri: str
    redirect_uri_explicit: bool
    scopes: str | None
    code_challenge: str | None
    resource: str | None
    state: str | None
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AuthCode:
    """One authorization code; `code` is caller-minted entropy, single use."""

    code: str
    client_id: str
    subject: str
    scopes: str | None
    code_challenge: str | None
    redirect_uri: str
    redirect_uri_explicit: bool
    resource: str | None
    expires_at: datetime
    used: bool = False


@dataclass(frozen=True, slots=True)
class AccessToken:
    """`token_hash` is `hash_token(raw)`; the raw access token is never stored."""

    token_hash: str
    family_id: str
    client_id: str
    subject: str
    scopes: str | None
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class RefreshToken:
    """`token_hash` is `hash_token(raw)`; revocation is a kept mark, not a delete."""

    token_hash: str
    family_id: str
    client_id: str
    subject: str
    scopes: str | None
    expires_at: datetime
    revoked: bool = False


def _pending(row: sqlite3.Row) -> PendingAuthz:
    return PendingAuthz(
        id=row["id"], client_id=row["client_id"], redirect_uri=row["redirect_uri"],
        redirect_uri_explicit=bool(row["redirect_uri_explicit"]), scopes=row["scopes"],
        code_challenge=row["code_challenge"], resource=row["resource"],
        state=row["state"], expires_at=_dt(row["expires_at"]))


def _auth_code(row: sqlite3.Row, raw_code: str) -> AuthCode:
    return AuthCode(
        code=raw_code, client_id=row["client_id"], subject=row["subject"],
        scopes=row["scopes"], code_challenge=row["code_challenge"],
        redirect_uri=row["redirect_uri"],
        redirect_uri_explicit=bool(row["redirect_uri_explicit"]),
        resource=row["resource"], expires_at=_dt(row["expires_at"]),
        used=bool(row["used"]))


def _access(row: sqlite3.Row) -> AccessToken:
    return AccessToken(
        token_hash=row["token_hash"], family_id=row["family_id"],
        client_id=row["client_id"], subject=row["subject"], scopes=row["scopes"],
        expires_at=_dt(row["expires_at"]))


def _refresh(row: sqlite3.Row) -> RefreshToken:
    return RefreshToken(
        token_hash=row["token_hash"], family_id=row["family_id"],
        client_id=row["client_id"], subject=row["subject"], scopes=row["scopes"],
        expires_at=_dt(row["expires_at"]), revoked=bool(row["revoked"]))


class Store:
    """Every method opens its own connection and commits or nothing changes.

    # allow: SIZE_OK — the plan prescribes this one file: a 6-table schema and
    # the persistence API for todo 6/7/8; the todo's file list forbids
    # splitting it across siblings inside remote/.
    """

    def __init__(self, path: Path, secret_key: str) -> None:
        try:
            self._fernet = Fernet(secret_key.encode("utf-8"))
        except ValueError as exc:
            raise ValueError(SECRET_KEY_MESSAGE) from exc
        self._path = path

    @contextmanager
    def _db(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            with conn:
                if immediate:
                    conn.execute("BEGIN IMMEDIATE")
                yield conn
        finally:
            conn.close()

    def init(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(_SCHEMA)
            for table, column, decl in _ADDED_COLUMNS:
                present = {row["name"] for row in
                           db.execute(f"PRAGMA table_info({table})")}
                if column not in present:
                    db.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    # ---------------------------------------------------------- OAuth clients
    def put_client(self, client: OAuthClientInformationFull) -> None:
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO oauth_clients VALUES (?, ?, ?)",
                (client.client_id,
                 self._fernet.encrypt(client.model_dump_json().encode()),
                 _iso(_now())))

    def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self._db() as db:
            row = db.execute(
                "SELECT client_json FROM oauth_clients WHERE client_id = ?",
                (client_id,)).fetchone()
        return (OAuthClientInformationFull.model_validate_json(
            self._fernet.decrypt(row[0])) if row else None)

    # -------------------------------------------------- pending authorisations
    def put_pending(self, row: PendingAuthz) -> None:
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO pending_authz VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row.id, row.client_id, row.redirect_uri, int(row.redirect_uri_explicit),
                 row.scopes, row.code_challenge, row.resource, row.state,
                 _iso(row.expires_at)))

    def pop_pending(self, pending_id: str) -> PendingAuthz | None:
        """Delete-on-read; an expired authorisation is refused and dropped."""
        with self._db() as db:
            if sqlite3.sqlite_version_info >= (3, 35):
                row = db.execute(
                    "DELETE FROM pending_authz WHERE id = ? RETURNING *",
                    (pending_id,)).fetchone()
            else:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM pending_authz WHERE id = ?",
                    (pending_id,)).fetchone()
                db.execute("DELETE FROM pending_authz WHERE id = ?", (pending_id,))
        if row is None or _dt(row["expires_at"]) <= _now():
            return None
        return _pending(row)

    def load_pending(self, pending_id: str) -> PendingAuthz | None:
        """Read a live pending authorization without consuming it."""
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM pending_authz WHERE id = ?", (pending_id,)).fetchone()
        return (_pending(row) if row is not None
                and _dt(row["expires_at"]) > _now() else None)

    # -------------------------------------------------------------- auth codes
    def put_code(self, row: AuthCode) -> None:
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO auth_codes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (hash_token(row.code), row.client_id, row.subject, row.scopes,
                 row.code_challenge,
                 row.redirect_uri, int(row.redirect_uri_explicit), row.resource,
                 _iso(row.expires_at), int(row.used)))

    def load_code(self, code: str) -> AuthCode | None:
        """A read that consumes nothing; unknown, used or expired reads as absent."""
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM auth_codes WHERE code = ?",
                (hash_token(code),)).fetchone()
            if row and _dt(row["expires_at"]) <= _now():
                db.execute("DELETE FROM auth_codes WHERE code = ?", (hash_token(code),))
                return None
        return _auth_code(row, code) if row and not row["used"] else None

    def consume_code(self, code: str) -> AuthCode | None:
        """Single use: atomically deletes and returns one live code."""
        code_hash = hash_token(code)
        with self._db() as db:
            if sqlite3.sqlite_version_info >= (3, 35):
                row = db.execute(
                    "DELETE FROM auth_codes WHERE code = ? RETURNING *",
                    (code_hash,)).fetchone()
            else:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM auth_codes WHERE code = ?",
                    (code_hash,)).fetchone()
                db.execute("DELETE FROM auth_codes WHERE code = ?", (code_hash,))
        if row is None or row["used"] or _dt(row["expires_at"]) <= _now():
            return None
        return _auth_code(row, code)

    @staticmethod
    def _put_pair(db: sqlite3.Connection, access: AccessToken,
                  refresh: RefreshToken) -> None:
        db.execute(
            "INSERT OR REPLACE INTO access_tokens VALUES (?, ?, ?, ?, ?, ?)",
            (access.token_hash, access.family_id, access.client_id, access.subject,
             access.scopes, _iso(access.expires_at)))
        db.execute(
            "INSERT OR REPLACE INTO refresh_tokens VALUES (?, ?, ?, ?, ?, ?, ?)",
            (refresh.token_hash, refresh.family_id, refresh.client_id,
             refresh.subject, refresh.scopes, _iso(refresh.expires_at),
             int(refresh.revoked)))

    def exchange_code_pair(self, code: str, access: AccessToken,
                           refresh: RefreshToken) -> AuthCode | None:
        """Consume a code and persist both minted tokens in one transaction."""
        with self._db(immediate=True) as db:
            row = db.execute(
                "SELECT * FROM auth_codes WHERE code = ?",
                (hash_token(code),)).fetchone()
            if row is None or row["used"] or _dt(row["expires_at"]) <= _now():
                db.execute("DELETE FROM auth_codes WHERE code = ?", (hash_token(code),))
                return None
            db.execute("DELETE FROM auth_codes WHERE code = ?", (hash_token(code),))
            self._put_pair(db, access, refresh)
        return _auth_code(row, code)

    def rotate_refresh_pair(self, token_hash: str, access: AccessToken,
                            refresh: RefreshToken) -> RefreshToken | None:
        """Revoke one refresh family and persist its replacement atomically."""
        with self._db(immediate=True) as db:
            row = db.execute(
                "SELECT * FROM refresh_tokens WHERE token_hash = ?",
                (token_hash,)).fetchone()
            if row is None or row["revoked"] or _dt(row["expires_at"]) <= _now():
                return None
            family_id = row["family_id"]
            db.execute("DELETE FROM access_tokens WHERE family_id = ?", (family_id,))
            db.execute("UPDATE refresh_tokens SET revoked = 1 WHERE family_id = ?",
                       (family_id,))
            self._put_pair(db, access, refresh)
        return _refresh(row)

    # ------------------------------------------------------------------ tokens
    def put_access(self, row: AccessToken) -> None:
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO access_tokens VALUES (?, ?, ?, ?, ?, ?)",
                (row.token_hash, row.family_id, row.client_id, row.subject,
                 row.scopes, _iso(row.expires_at)))

    def load_access(self, token_hash: str) -> AccessToken | None:
        """Expired rows delete themselves on read; the hash lookup misses them."""
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM access_tokens WHERE token_hash = ?",
                (token_hash,)).fetchone()
            if row and _dt(row["expires_at"]) <= _now():
                db.execute(
                    "DELETE FROM access_tokens WHERE token_hash = ?", (token_hash,))
                return None
        return _access(row) if row else None

    def put_refresh(self, row: RefreshToken) -> None:
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO refresh_tokens VALUES (?, ?, ?, ?, ?, ?, ?)",
                (row.token_hash, row.family_id, row.client_id, row.subject,
                 row.scopes, _iso(row.expires_at), int(row.revoked)))

    def load_refresh(self, token_hash: str) -> RefreshToken | None:
        """Revoked reads as absent (the mark stays); expired deletes the row."""
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM refresh_tokens WHERE token_hash = ?",
                (token_hash,)).fetchone()
            if row is None or row["revoked"]:
                return None
            if _dt(row["expires_at"]) <= _now():
                db.execute(
                    "DELETE FROM refresh_tokens WHERE token_hash = ?", (token_hash,))
                return None
        return _refresh(row)

    def revoke_family(self, family_id: str) -> None:
        """Kill a minted pair and every rotation of it: accesses die now,
        refreshes keep a revocation mark so a presented one reads as dead."""
        with self._db(immediate=True) as db:
            db.execute("DELETE FROM access_tokens WHERE family_id = ?", (family_id,))
            db.execute(
                "UPDATE refresh_tokens SET revoked = 1 WHERE family_id = ?",
                (family_id,))

    # ----------------------------------------------------------------- tenants
    def put_tenant(self, t: Tenant) -> None:
        """Seal the API key; created_at and last_used_at start now."""
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO tenants (subject, base_url, key_hash,"
                " api_key_enc, db, policy, created_at, last_used_at, login)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (t.subject, t.base_url, key_hash(t.base_url, t.api_key),
                 self._fernet.encrypt(t.api_key.encode()), t.db, t.policy,
                 _iso(_now()), _iso(_now()), t.login))

    def _tenant(self, row: sqlite3.Row) -> Tenant:
        api_key = self._fernet.decrypt(row["api_key_enc"]).decode()
        # A row written before the column existed reads NULL; the client wants
        # the empty string, which is what "no login, discover it" spells.
        return Tenant(row["subject"], row["base_url"], api_key, row["db"],
                      row["policy"], row["login"] or "")

    def get_tenant(self, subject: str) -> Tenant | None:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM tenants WHERE subject = ?", (subject,)).fetchone()
        return self._tenant(row) if row else None

    def find_tenant_by_key_hash(self, keyhash: str) -> Tenant | None:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM tenants WHERE key_hash = ?", (keyhash,)).fetchone()
        return self._tenant(row) if row else None

    def touch_tenant(self, subject: str) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE tenants SET last_used_at = ? WHERE subject = ?",
                (_iso(_now()), subject))

    def delete_tenant(self, subject: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM tenants WHERE subject = ?", (subject,))
        tenant_context.forget(subject)

    # ------------------------------------------------------------ housekeeping
    def tokens_alive(self, subject: str) -> bool:
        """True while any unrevoked, unexpired token of the subject exists —
        the check behind 'disconnect in the host deletes the stored Odoo key'."""
        now = _iso(_now())
        with self._db() as db:
            row = db.execute(
                "SELECT EXISTS(SELECT 1 FROM access_tokens"
                "              WHERE subject = :s AND expires_at > :n)"
                " OR EXISTS(SELECT 1 FROM refresh_tokens"
                "              WHERE subject = :s AND revoked = 0 AND expires_at > :n)",
                {"s": subject, "n": now}).fetchone()
        return bool(row[0])

    def purge_expired(self) -> None:
        """Drop every unreadable row: expired pending, codes and tokens, plus
        refresh tokens already revoked. Runs at startup and periodically."""
        now = _iso(_now())
        with self._db() as db:
            db.execute("DELETE FROM pending_authz WHERE expires_at <= ?", (now,))
            db.execute("DELETE FROM auth_codes WHERE expires_at <= ?", (now,))
            db.execute("DELETE FROM access_tokens WHERE expires_at <= ?", (now,))
            db.execute(
                "DELETE FROM refresh_tokens WHERE revoked = 1 OR expires_at <= ?",
                (now,))

    def purge_idle_tenants(self, days: int = 90) -> list[str]:
        """Forget tenants unused for `days` that hold no live token — the
        privacy page's 'automatically after 90 days without use'. Returns
        the removed subjects so the caller can purge their disk artifacts."""
        with self._db(immediate=True) as db:
            params = {"cutoff": _iso(_now() - timedelta(days=days)),
                      "now": _iso(_now())}
            subjects = [row[0] for row in db.execute(
                "SELECT subject FROM tenants WHERE last_used_at <= :cutoff"
                " AND NOT EXISTS (SELECT 1 FROM access_tokens a"
                "                 WHERE a.subject = tenants.subject"
                "                   AND a.expires_at > :now)"
                " AND NOT EXISTS (SELECT 1 FROM refresh_tokens r"
                "                 WHERE r.subject = tenants.subject AND r.revoked = 0"
                "                   AND r.expires_at > :now)", params).fetchall()]
            db.execute(
                "DELETE FROM tenants WHERE last_used_at <= :cutoff"
                " AND NOT EXISTS (SELECT 1 FROM access_tokens a"
                "                 WHERE a.subject = tenants.subject"
                "                   AND a.expires_at > :now)"
                " AND NOT EXISTS (SELECT 1 FROM refresh_tokens r"
                "                 WHERE r.subject = tenants.subject AND r.revoked = 0"
                "                   AND r.expires_at > :now)",
                params)
        for subject in subjects:
            tenant_context.forget(subject)
        return subjects
