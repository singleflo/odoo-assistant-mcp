#!/usr/bin/env python3
"""odoo-assistant-remote — the hosted, multi-tenant server.

One process serves many tenants, each with their own Odoo credentials stored
via the consent page. The architecture is fixed by three measured facts:

* **The only tenant binding is a JSON-RPC-tier `ServerMiddleware`.**
  `streamable_http_app()` accepts no user Starlette middleware and builds
  `AuthenticationMiddleware` + `AuthContextMiddleware` internally, so any
  Starlette middleware would run BEFORE the auth contextvar is set and
  `auth_context.get_access_token()` would always be None there. `BindTenant`
  therefore runs inside the JSON-RPC dispatch, where the contextvar is set;
  anyio copies that context into worker threads, so sync tool bodies read
  `tenant.current()` too.
* **`stateless_http=True` is mandatory.** In a stateful session the
  initializing request's tenant would be pinned to every later message on
  that session — two tenants behind one host would swap instances. Stateless
  mode re-binds per JSON-RPC message.
* **The 401 for a token whose tenant is gone comes from `load_access_token`
  returning None** (remote/auth.py checks the tenant row), which makes the
  SDK's `RequireAuthMiddleware` emit 401 + a `WWW-Authenticate` header with
  `resource_metadata=`. `BindTenant`'s own missing-tenant error is only the
  defensive second line (a ServerMiddleware can only answer inside HTTP 200).

Wiring constraint from todo 7: `consent.py` handlers read
`request.app.state.consent_deps`, and `request.app` is the app
`streamable_http_app()` RETURNS — the state and the custom routes both live
there. Custom routes are consumed when `streamable_http_app()` runs, so they
are registered BEFORE it is called.

Lifespan: the SDK's returned app already installs a lifespan that starts the
streamable-HTTP session manager (`router.lifespan_context`), and Starlette
never runs a mounted app's lifespan — so the startup purges are wrapped
around the installed context rather than replacing or remounting it.

A multi-tenant process must never carry one tenant's Odoo environment:
`connect()` falls back to `os.environ` for db/user, so a stale `ODOO_DB`
would route every tenant to one database. `build_app` refuses to start when
any `ODOO_BASE_URL` / `ODOO_API_KEY` / `ODOO_DB` / `ODOO_USER` is set.

# allow: SIZE_OK — the plan prescribes exactly this one file for the whole
# hosted-server assembly (routes, settings, middleware, lifespan) and forbids
# new siblings under remote/; the constraint documentation above is mandated.
"""
import html
import logging
import os
import re
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable
from urllib.parse import urlsplit

from mcp.server import MCPServer
from mcp.server.auth.middleware import auth_context
from mcp.server.auth.settings import (
    AuthSettings,
    ClientRegistrationOptions,
    RevocationOptions,
)
from mcp.server.context import (
    CallNext,
    HandlerResult,
    ServerRequestContext,
)
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_REQUEST
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)

from odoo_assistant import paths, resources, tenant
from odoo_assistant.remote import consent, files
from odoo_assistant.remote.auth import OdooAssistantAuthProvider
from odoo_assistant.remote.store import Store
from odoo_assistant import tools_collab, tools_discuss, tools_evolution, tools_read, tools_write
from odoo_assistant.server import _VERSION

REPO_URL = "https://github.com/singleflo/odoo-assistant-mcp"
INSTRUCTIONS = (
    "An Odoo virtual employee: query, count, create, update and act on the "
    "records of the Odoo instance the user connected during authorization, "
    "summarise the instance, notify colleagues, render PDFs and generate "
    "reference documentation. The connection's URL, API key and write policy "
    "come from the consent page at setup, not from this conversation."
)

# Refused before anything else starts: one of these in the environment turns
# the multi-tenant server into a quiet proxy onto one Odoo database.
_TENANT_ENV_VARS = ("ODOO_BASE_URL", "ODOO_API_KEY", "ODOO_DB", "ODOO_USER")

_HEADING = re.compile(r"(#{1,4})\s+(.*)")
_BULLET = re.compile(r"[-*]\s+(.*)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")


def _refuse_shared_odoo_env() -> None:
    for name in _TENANT_ENV_VARS:
        if os.environ.get(name):
            raise RuntimeError(
                f"{name} is set in the environment; odoo-assistant-remote is"
                " multi-tenant and must not carry one tenant's Odoo"
                " credentials. Unset it (credentials are stored per tenant"
                " via the consent page).")


@dataclass(frozen=True, slots=True)
class RemoteSettings:
    """The hosted server's configuration, parsed once from the environment."""

    public_url: str
    secret_key: str
    host: str
    port: int
    openai_challenge: str | None
    publisher: str
    support_email: str
    data_dir: Path
    allow_private_targets: bool

    @classmethod
    def from_env(cls) -> "RemoteSettings":
        public_url = os.environ.get("ODOO_REMOTE_PUBLIC_URL", "").rstrip("/")
        if not public_url:
            raise RuntimeError(
                "ODOO_REMOTE_PUBLIC_URL is required (e.g."
                " https://mcp.example.com); it is the address clients"
                " connect to.")
        parts = urlsplit(public_url)
        host = parts.hostname or ""
        if parts.scheme != "https" and not (
                parts.scheme == "http" and host in ("localhost", "127.0.0.1")):
            raise RuntimeError(
                "ODOO_REMOTE_PUBLIC_URL must be an https:// URL (or"
                " http://localhost for local runs), got"
                f" {public_url!r}.")
        secret_key = os.environ.get("ODOO_REMOTE_SECRET_KEY", "")
        if not secret_key:
            raise RuntimeError(
                "ODOO_REMOTE_SECRET_KEY is required; generate one with:"
                " python -c 'from cryptography.fernet import Fernet;"
                " print(Fernet.generate_key().decode())'")
        return cls(
            public_url=public_url,
            secret_key=secret_key,
            host=os.environ.get("ODOO_REMOTE_HOST", "0.0.0.0"),
            # PORT overrides; otherwise listen on the public URL's own port,
            # so ODOO_REMOTE_PUBLIC_URL=https://host:8443 is reachable as is.
            port=int(os.environ.get(
                "PORT", str(parts.port or 8000))),
            openai_challenge=os.environ.get("ODOO_REMOTE_OPENAI_CHALLENGE"),
            publisher=os.environ.get(
                "ODOO_REMOTE_PUBLISHER", "the odoo-assistant maintainers"),
            support_email=os.environ.get(
                "ODOO_REMOTE_SUPPORT_EMAIL", f"{REPO_URL}/issues"),
            data_dir=paths.data_dir(),
            allow_private_targets=(
                os.environ.get("ODOO_REMOTE_ALLOW_PRIVATE_TARGETS") == "1"),
        )


class BindTenant:
    """Binds the request's tenant around every JSON-RPC message.

    The store lookup is the same sub-millisecond SQLite read the auth
    provider already does; the bind/reset bracket is the only thing standing
    between two tenants sharing this process.
    """

    def __init__(self, store: Store) -> None:
        self._store = store

    async def __call__(self, ctx: ServerRequestContext[Any, Any],
                       call_next: CallNext) -> HandlerResult:
        token = auth_context.get_access_token()
        bound = (self._store.get_tenant(token.subject)
                 if token is not None and token.subject else None)
        if bound is None:
            # Defensive only: the real 401 comes from load_access_token
            # returning None. Tell the client to start a fresh authorization.
            raise MCPError(
                code=INVALID_REQUEST,
                message="Your connection is no longer authorized on this"
                        " server. Reconnect to start a new authorization.")
        t = tenant.bind(bound)
        try:
            return await call_next(ctx)
        finally:
            tenant.reset(t)


def _inline_md(text: str) -> str:
    escaped = html.escape(text, quote=False)
    linked = _LINK.sub(r'<a href="\2">\1</a>', escaped)
    return _BOLD.sub(r"<strong>\1</strong>", linked)


def _markdown_to_html(md: str) -> str:
    """Headings, bullet lists, paragraphs, links, bold. No dependency."""
    out: list[str] = []
    in_list = False
    for line in md.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        heading = _HEADING.match(stripped)
        bullet = _BULLET.match(stripped)
        if heading:
            if in_list:
                out.append("</ul>")
                in_list = False
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline_md(heading.group(2))}</h{level}>")
        elif bullet:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline_md(bullet.group(1))}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p>{_inline_md(stripped)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _serve_page(name: str, settings: RemoteSettings) -> Response:
    source = (resources.files("odoo_assistant.remote.pages")
              / f"{name}.md").read_text(encoding="utf-8")
    source = (source.replace("{{PUBLISHER}}", settings.publisher)
              .replace("{{SUPPORT_EMAIL}}", settings.support_email))
    return HTMLResponse(
        "<html><head><meta charset=\"utf-8\">"
        f"<title>odoo-assistant {name}</title></head><body>"
        f"{_markdown_to_html(source)}</body></html>")


def _landing_page(settings: RemoteSettings) -> Response:
    return HTMLResponse(
        "<html><head><meta charset=\"utf-8\">"
        "<title>odoo-assistant hosted server</title></head><body>"
        "<h1>odoo-assistant — hosted MCP server for Odoo</h1>"
        "<p>This is a Model Context Protocol server that gives AI clients"
        " like Claude or ChatGPT supervised access to YOUR Odoo instance:"
        " search and read records, create and update them within the policy"
        " you chose, render PDFs, notify colleagues and generate reference"
        " documentation.</p>"
        "<p><strong>How to connect:</strong> point your MCP client at"
        f" <code>{html.escape(settings.public_url)}/mcp</code>. The client"
        " walks you through an OAuth sign-in; you then enter your own Odoo"
        " URL and API key on the consent page, and pick what the assistant"
        " may do (read only, or standard). Nothing is shared between"
        " users.</p>"
        "<p><a href=\"/privacy\">Privacy notice</a> &middot;"
        " <a href=\"/terms\">Terms</a> &middot;"
        " <a href=\"/support\">Support</a></p>"
        "</body></html>")


def build_app(settings: RemoteSettings) -> Starlette:
    """The complete hosted server: MCP at /mcp plus the consent, files and
    pages routes, with every request bound to its tenant."""
    _refuse_shared_odoo_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(settings.data_dir / "remote.db", settings.secret_key)
    store.init()
    provider = OdooAssistantAuthProvider(store, settings.public_url)

    mcp = MCPServer(
        "odoo-assistant",
        version=_VERSION,
        instructions=INSTRUCTIONS,
        website_url=REPO_URL,
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(settings.public_url),
            resource_server_url=AnyHttpUrl(f"{settings.public_url}/mcp"),
            required_scopes=["odoo"],
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=["odoo"], default_scopes=["odoo"]),
            revocation_options=RevocationOptions(enabled=True),
        ),
        middleware=[BindTenant(store)],
    )
    tools_read.register(mcp)
    tools_write.register(mcp)
    tools_collab.register(mcp)
    tools_discuss.register(mcp)
    tools_evolution.register(mcp)
    resources.register(mcp)

    mcp.custom_route("/consent", methods=["GET"])(consent.consent_form)
    mcp.custom_route("/consent", methods=["POST"])(consent.consent_submit)
    mcp.custom_route("/files/{token}", methods=["GET"])(files.serve_file)

    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok", "version": _VERSION})

    mcp.custom_route("/health", methods=["GET"])(health)

    async def challenge(request: Request) -> Response:
        if settings.openai_challenge is None:
            return PlainTextResponse("Not Found", status_code=404)
        return PlainTextResponse(settings.openai_challenge)

    mcp.custom_route("/.well-known/openai-apps-challenge", methods=["GET"])(
        challenge)

    async def landing(request: Request) -> Response:
        return _landing_page(settings)

    mcp.custom_route("/", methods=["GET"])(landing)
    for name in ("privacy", "terms", "support"):
        mcp.custom_route(f"/{name}", methods=["GET"])(
            _page_handler(name, settings))

    host = urlsplit(settings.public_url).hostname or "localhost"
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,  # mandatory: a stateful session pins one tenant
        host=settings.host,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[host, f"{host}:*"],
            allowed_origins=[
                settings.public_url, "https://claude.ai", "https://chatgpt.com"],
        ),
    )
    # consent.py reads request.app.state.consent_deps; request.app is THIS
    # app (custom routes join it unmounted). Set it on the returned app,
    # after streamable_http_app() has run.
    app.state.consent_deps = consent.ConsentDeps(
        store=store, provider=provider, public_url=settings.public_url,
        allow_private_targets=settings.allow_private_targets)

    # Wrap, not replace: the session manager only starts through the
    # lifespan the SDK app already installed.
    session_manager_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        store.purge_expired()
        store.purge_idle_tenants()
        files.purge_expired_files()
        async with session_manager_lifespan(app):
            yield

    app.router.lifespan_context = lifespan
    return app


def _page_handler(name: str,
                  settings: RemoteSettings
                  ) -> Callable[[Request], Awaitable[Response]]:
    async def handler(request: Request) -> Response:
        return _serve_page(name, settings)
    return handler


def main() -> None:
    """Entry point of the `odoo-assistant-remote` console script."""
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = RemoteSettings.from_env()
    import uvicorn

    uvicorn.run(build_app(settings), host=settings.host, port=settings.port,
                log_config=None)


if __name__ == "__main__":
    main()
