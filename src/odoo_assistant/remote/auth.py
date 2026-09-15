"""The hosted server's OAuth 2.1 authorization decisions, backed by the Store.

Every DCR client, pending authorization, code, token pair and revocation the
SDK's `/register`, `/authorize`, `/token` and `/revoke` routes take is decided
here and persisted through `remote.store` — tokens only ever touch the disk as
sha256 hashes, per the Store's minting contract: this module mints every
pending id, authorization code, access token, refresh token and family id with
`secrets.token_urlsafe(24)` (192 bits).

Store calls stay synchronous on purpose: each is a plain SQLite point
operation on a per-call connection (Fernet over a few hundred bytes at most),
sub-millisecond in practice, and the plan allows exactly that.

The tenant linkage is the retention story: a token validates only while its
subject's tenant row still exists (a host disconnect revokes, which deletes
the tenant when no family is left), and every successful validation refreshes
the tenant's `last_used_at` so the 90-day idle purge never takes a live one.
"""
from datetime import datetime, timezone
import secrets

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from odoo_assistant.remote import store
from odoo_assistant import tenant


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OdooAssistantAuthProvider(
        OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """The SDK authorization-server hooks over one `Store`, at one public URL."""

    def __init__(self, store: store.Store, public_url: str) -> None:
        self._store = store
        self._public_url = public_url

    # ------------------------------------------------------------- clients
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._store.get_client(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._store.put_client(client_info)

    # ---------------------------------------------------------- authorize
    async def authorize(self, client: OAuthClientInformationFull,
                        params: AuthorizationParams) -> str:
        """Park the request as a pending row; the consent page finishes it."""
        pending_id = secrets.token_urlsafe(24)
        self._store.put_pending(store.PendingAuthz(
            id=pending_id,
            client_id=client.client_id,
            redirect_uri=str(params.redirect_uri),
            redirect_uri_explicit=params.redirect_uri_provided_explicitly,
            scopes=" ".join(params.scopes) if params.scopes else None,
            code_challenge=params.code_challenge,
            resource=params.resource,
            state=params.state,
            expires_at=_now() + store.PENDING_TTL))
        return construct_redirect_uri(f"{self._public_url}/consent", req=pending_id)

    def complete_consent(self, pending_id: str, subject: str) -> str:
        """Mint the authorization code for a consented pending request.

        Called by the consent page (todo 7) once the user approved; `subject`
        is the tenant the consent assigned. Returns the client's final
        redirect URL carrying `code` and `state`.
        """
        pending = self._store.pop_pending(pending_id)
        if pending is None:
            raise ValueError("unknown or expired authorization request")
        code = secrets.token_urlsafe(24)
        self._store.put_code(store.AuthCode(
            code=code,
            client_id=pending.client_id,
            subject=subject,
            scopes=pending.scopes,
            code_challenge=pending.code_challenge,
            redirect_uri=pending.redirect_uri,
            redirect_uri_explicit=pending.redirect_uri_explicit,
            resource=pending.resource,
            expires_at=_now() + store.CODE_TTL))
        return construct_redirect_uri(pending.redirect_uri, code=code,
                                      state=pending.state)

    # ------------------------------------------------------ code exchange
    async def load_authorization_code(
            self, client: OAuthClientInformationFull,
            authorization_code: str) -> AuthorizationCode | None:
        row = self._store.load_code(authorization_code)
        if row is None or row.client_id != client.client_id:
            return None
        return AuthorizationCode(
            code=row.code,
            scopes=row.scopes.split() if row.scopes else [],
            expires_at=row.expires_at.timestamp(),
            client_id=row.client_id,
            code_challenge=row.code_challenge or "",
            redirect_uri=AnyUrl(row.redirect_uri),
            redirect_uri_provided_explicitly=row.redirect_uri_explicit,
            resource=row.resource,
            subject=row.subject)

    async def exchange_authorization_code(
            self, client: OAuthClientInformationFull,
            authorization_code: AuthorizationCode) -> OAuthToken:
        if authorization_code.subject is None:
            raise TokenError(error="invalid_grant",
                             error_description="authorization code has no subject")
        pair, access, refresh = self._new_pair(
            client.client_id, authorization_code.subject,
            " ".join(authorization_code.scopes), secrets.token_urlsafe(24))
        row = self._store.exchange_code_pair(
            authorization_code.code, access, refresh)
        if row is None:
            raise TokenError(error="invalid_grant",
                             error_description="authorization code is used or expired")
        return pair

    # ---------------------------------------------------- refresh rotation
    async def load_refresh_token(
            self, client: OAuthClientInformationFull,
            refresh_token: str) -> RefreshToken | None:
        row = self._store.load_refresh(store.hash_token(refresh_token))
        if row is None or row.client_id != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row.client_id,
            scopes=row.scopes.split() if row.scopes else [],
            expires_at=int(row.expires_at.timestamp()),
            subject=row.subject)

    async def exchange_refresh_token(
            self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
            scopes: list[str]) -> OAuthToken:
        row = self._store.load_refresh(store.hash_token(refresh_token.token))
        if row is None:
            raise TokenError(error="invalid_grant",
                             error_description="refresh token is revoked or expired")
        # Rotation: the presented token (and its family's accesses) die now;
        # the replacement pair is minted in the SAME family, so a later
        # revocation of either still reaches everything ever issued from it.
        pair, access, replacement = self._new_pair(
            client.client_id, row.subject, " ".join(scopes), row.family_id)
        rotated = self._store.rotate_refresh_pair(
            store.hash_token(refresh_token.token), access, replacement)
        if rotated is None:
            raise TokenError(error="invalid_grant",
                             error_description="refresh token is revoked or expired")
        return pair

    # ------------------------------------------------------ access tokens
    async def load_access_token(self, token: str) -> AccessToken | None:
        row = self._store.load_access(store.hash_token(token))
        if row is None or self._store.get_tenant(row.subject) is None:
            return None
        self._store.touch_tenant(row.subject)
        return AccessToken(
            token=token,
            client_id=row.client_id,
            scopes=row.scopes.split() if row.scopes else [],
            expires_at=int(row.expires_at.timestamp()),
            subject=row.subject)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        token_hash = store.hash_token(token.token)
        row = self._store.load_access(token_hash)
        if row is None:
            row = self._store.load_refresh(token_hash)
        if row is None:
            return  # unknown or already revoked: RFC 7009 says do nothing
        # Either half kills the whole family, then a disconnect (no family
        # left) erases the stored Odoo connection with it.
        self._store.revoke_family(row.family_id)
        if not self._store.tokens_alive(row.subject):
            self._store.delete_tenant(row.subject)
            tenant.forget(row.subject)

    # ------------------------------------------------------------ internals
    def _new_pair(self, client_id: str, subject: str, scopes: str | None,
                  family_id: str) -> tuple[OAuthToken, store.AccessToken,
                                           store.RefreshToken]:
        now = _now()
        access = secrets.token_urlsafe(24)
        refresh = secrets.token_urlsafe(24)
        access_row = store.AccessToken(
            token_hash=store.hash_token(access),
            family_id=family_id,
            client_id=client_id,
            subject=subject,
            scopes=scopes,
            expires_at=now + store.ACCESS_TTL)
        refresh_row = store.RefreshToken(
            token_hash=store.hash_token(refresh),
            family_id=family_id,
            client_id=client_id,
            subject=subject,
            scopes=scopes,
            expires_at=now + store.REFRESH_TTL)
        pair = OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=int(store.ACCESS_TTL.total_seconds()),
            refresh_token=refresh,
            scope=scopes)
        return pair, access_row, refresh_row
