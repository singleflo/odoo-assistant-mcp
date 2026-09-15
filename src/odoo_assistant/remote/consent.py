"""The consent page where a user hands over their own Odoo credentials.

Two plain Starlette handlers, public by SDK design (custom routes are never
authenticated — `mcpserver/server.py:1030`). Everything the handlers need
travels in one `ConsentDeps` object read from `app.state.consent_deps`; no
module singletons, so a test or a second deployment builds its own. Wiring
for todo 8 — state goes on the SAME Starlette app the routes are added to,
i.e. the one `streamable_http_app()` returns:

    http_app = mcp.streamable_http_app(...)
    http_app.state.consent_deps = ConsentDeps(
        store=store, provider=provider, public_url=public_url,
        allow_private_targets=(
            os.environ.get("ODOO_REMOTE_ALLOW_PRIVATE_TARGETS") == "1"))
    mcp.custom_route("/consent", methods=["GET"])(consent.consent_form)
    mcp.custom_route("/consent", methods=["POST"])(consent.consent_submit)

Security posture, in the order the POST enforces it: a 64 KiB body cap
(custom routes sit outside the SDK's 4 MiB `RequestBodyLimitMiddleware`),
the pending-`req` check, the https rule, the SSRF guard — the host is
resolved with `socket.getaddrinfo` and refused unless every address is global,
BEFORE any connection, so a public
unauthenticated endpoint can never be turned into an internal prober — then
a real verification against the submitted Odoo, run in its own spawn
SUBPROCESS and reaped at the deadline: the verified scripts carry no socket
timeout and are canonical (never rewritten here), so a host that accepts the
socket and stalls would otherwise keep a worker thread blocked forever; a
process can be terminated and killed instead — the HTTP request always
answers within the timeout, leaving nothing hanging. Then the tenant row,
reused by `key_hash` so one Odoo connection stays one subject. The key is
never logged and never echoed; every interpolated value, the Odoo error text
included, goes through `html.escape` — it is untrusted external text landing
in a browser page.
"""
import html
import ipaddress
import logging
import multiprocessing
import secrets
import socket
import sys
from dataclasses import dataclass, replace
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import parse_qs, urlsplit

from anyio.to_thread import run_sync as run_in_thread
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from odoo_assistant.remote.store import PendingAuthz, Store, key_hash
from odoo_assistant.tenant import Tenant

# Same bootstrap as server.py and tenant.py: the scripts are flat modules
# that import each other by bare name, from the repo and from a wheel.
sys.path.insert(0, str(Path(__file__).parent.parent / "odoo_scripts"))

from odoo_client import connect  # noqa: E402  (needs the bootstrap above)

logger = logging.getLogger(__name__)

_MAX_BODY = 64 * 1024
_VERIFY_TIMEOUT = 20
_TERMINATE_GRACE = 2.0
_GONE_MESSAGE = ("This connection request is unknown or has expired."
                 " Start again from your AI assistant.")


class ConsentProvider(Protocol):
    """The one auth-provider method consent needs (todo 6 owns the class)."""

    def complete_consent(self, pending_id: str, subject: str) -> str: ...


@dataclass(frozen=True, slots=True)
class ConsentDeps:
    """The handlers' whole world, attached to `app.state.consent_deps`."""

    store: Store
    provider: ConsentProvider
    public_url: str
    allow_private_targets: bool


@dataclass(frozen=True, slots=True)
class _FormState:
    """What the form shows on a (re)render: the pending id, the previous
    submission minus the key, and the error explaining the re-render."""

    req: str
    odoo_url: str = ""
    db: str = ""
    policy: str = "read"
    error: str | None = None


async def consent_form(request: Request) -> Response:
    """GET /consent?req=<id>: the page a user fills in with their own Odoo."""
    deps: ConsentDeps = request.app.state.consent_deps
    pending = _peek_pending(deps.store, request.query_params.get("req", ""))
    if pending is None:
        return _plain_page(_GONE_MESSAGE, status_code=400)
    return HTMLResponse(_form_html(deps, _FormState(req=pending.id)))


async def consent_submit(request: Request) -> Response:
    """POST /consent: verify the credentials, store the tenant, and send the
    browser on to the client's redirect_uri with a fresh code."""
    deps: ConsentDeps = request.app.state.consent_deps
    fields = await _bounded_fields(request)
    if fields is None:
        return Response("Request body too large.", status_code=413)

    pending = _peek_pending(deps.store, fields.get("req", ""))
    if pending is None:
        return _plain_page(_GONE_MESSAGE, status_code=400)

    odoo_url = fields.get("odoo_url", "").strip().rstrip("/")
    api_key = fields.get("api_key", "")
    db = fields.get("db", "").strip()
    policy = fields.get("policy", "")
    shown = _FormState(req=pending.id, odoo_url=odoo_url, db=db, policy="read")
    if not odoo_url or not api_key:
        return HTMLResponse(_form_html(deps, replace(
            shown, error="Fill in the Odoo URL and the API key.")))
    if policy != "read" and policy != "standard":
        return HTMLResponse(_form_html(deps, replace(
            shown, error="Choose what the assistant may do.")))
    shown = replace(shown, policy=policy)

    parts = urlsplit(odoo_url)
    host = parts.hostname or ""
    https_ok = (parts.scheme == "https"
                or (parts.scheme == "http" and host == "localhost"))
    if not host or not https_ok:
        return HTMLResponse(_form_html(deps, replace(shown, error=(
            "The Odoo URL must be an https:// address (http://localhost"
            " works for a server on this machine)."))))

    if not deps.allow_private_targets:
        refusal = await run_in_thread(
            _ssrf_refusal, host, abandon_on_cancel=True)
        if refusal is not None:
            return HTMLResponse(_form_html(
                deps, replace(shown, error=refusal)))

    try:
        # Bounded by construction: the thread joins the child for at most
        # _VERIFY_TIMEOUT (+ the reap), so a cancelled request waits out at
        # most that, and the terminate/kill bookkeeping is never half-done.
        outcome = await run_in_thread(_verify_isolated, odoo_url, db, api_key)
    except Exception as exc:
        logger.info("consent: verification failed for host %s", host)
        return HTMLResponse(_form_html(deps, replace(
            shown, error=str(exc) or "The Odoo server refused the"
            " connection.")))
    match outcome.status:
        case "timeout":
            logger.info("consent: no answer from host %s", host)
            return HTMLResponse(_form_html(
                deps, replace(shown, error=outcome.detail)))
        case "error":
            logger.info("consent: verification failed for host %s", host)
            return HTMLResponse(_form_html(deps, replace(
                shown, error=outcome.detail or "The Odoo server refused the"
                " connection.")))
        case "ok":
            pass

    existing = deps.store.find_tenant_by_key_hash(key_hash(odoo_url, api_key))
    if existing is not None:
        subject = existing.subject
        deps.store.put_tenant(replace(existing, db=db, policy=policy))
    else:
        subject = "t_" + secrets.token_urlsafe(16)
        deps.store.put_tenant(Tenant(subject=subject, base_url=odoo_url,
                                     api_key=api_key, db=db, policy=policy))
    logger.info("consent: subject %s connected to host %s", subject, host)
    return RedirectResponse(
        deps.provider.complete_consent(pending.id, subject), status_code=302)


async def _bounded_fields(request: Request) -> dict[str, str] | None:
    """The form fields of a body capped at 64 KiB, or None when oversize.

    Refusing at the first chunk past the cap keeps an oversized POST from
    being read into memory at all.
    """
    size = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        size += len(chunk)
        if size > _MAX_BODY:
            return None
        chunks.append(chunk)
    parsed = parse_qs(b"".join(chunks).decode("utf-8", "replace"))
    return {name: values[0] for name, values in parsed.items()}


def _peek_pending(store: Store, pending_id: str) -> PendingAuthz | None:
    """Look at a pending authorisation without consuming it.

    `pop_pending` is delete-on-read (that is the token flow's consume), so a
    live row is put straight back: the page must survive a failed
    verification and a resubmit. `complete_consent` does the real consume.
    """
    return store.load_pending(pending_id)


def _ssrf_refusal(host: str) -> str | None:
    """Why this host may not be dialed from a public endpoint, or None.

    Runs before any connection: consent is unauthenticated, so without this
    guard anyone could make the server probe internal ranges and the cloud
    metadata address on their own schedule.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return f"Could not resolve the Odoo host: {exc}"
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not addr.is_global:
            return "The Odoo URL must be a public host."
    return None


def _verify_credentials(odoo_url: str, db: str, api_key: str) -> None:
    """Prove the key works: connect (the JSON-2 path authenticates with the
    key alone), then read the key owner's login."""
    odoo = connect(base=odoo_url, db=db, user="", key=api_key)
    odoo.call("res.users", "read", [[odoo.uid], ["login"]], {})


@dataclass(frozen=True, slots=True)
class _Verified:
    """What one isolated verification concluded. The process travels along
    so a test (or a supervisor) can confirm nothing survives the call."""

    status: Literal["ok", "error", "timeout"]
    detail: str
    process: multiprocessing.process.BaseProcess | None


def _verify_entry(odoo_url: str, db: str, api_key: str,
                  send_conn: Connection) -> None:
    """Child side: one verification, then the outcome on the pipe — "ok" or
    the exception repr. `connect` resolves through this module, which the
    spawn interpreter imports fresh; nothing unpicklable crosses, only the
    three strings and the pipe."""
    try:
        _verify_credentials(odoo_url, db, api_key)
        send_conn.send(("ok", ""))
    except Exception as exc:
        send_conn.send(("error", repr(exc)))
    finally:
        send_conn.close()


_last_verifier: multiprocessing.process.BaseProcess | None = None


def _verify_isolated(odoo_url: str, db: str, api_key: str) -> _Verified:
    """One credential check in its own short-lived spawn process.

    The verified scripts (`odoo_scripts/`, canonical and never rewritten
    here) carry no socket timeout, so a host that accepts the connection and
    stalls would block a worker thread forever. A subprocess instead is
    joined with a hard deadline and then reaped — the caller always gets an
    answer within `_VERIFY_TIMEOUT`, and no process survives this call.
    `_last_verifier` holds the most recent child's handle.
    """
    global _last_verifier
    ctx = multiprocessing.get_context("spawn")
    recv_conn, send_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_verify_entry,
                       args=(odoo_url, db, api_key, send_conn), daemon=True)
    _last_verifier = proc
    proc.start()
    send_conn.close()  # the parent keeps no write end: EOF means the child died
    try:
        proc.join(_VERIFY_TIMEOUT)
        if proc.is_alive():
            _reap(proc)
            return _Verified(
                "timeout",
                f"The Odoo server did not answer within"
                f" {_VERIFY_TIMEOUT:g} seconds.", proc)
        try:
            status, detail = recv_conn.recv()
        except EOFError:  # the child died without answering
            return _Verified("error", "", proc)
        return _Verified(status, detail, proc)
    finally:
        recv_conn.close()


def _reap(proc: multiprocessing.process.BaseProcess) -> None:
    """Terminate, then kill: a hung socket read must never outlive the
    request that started it."""
    proc.terminate()
    proc.join(_TERMINATE_GRACE)
    if proc.is_alive():
        proc.kill()
        proc.join(_TERMINATE_GRACE)


def _plain_page(message: str, status_code: int) -> HTMLResponse:
    return HTMLResponse(
        _page("Odoo Assistant", f"<p>{html.escape(message)}</p>"),
        status_code=status_code)


def _form_html(deps: ConsentDeps, shown: _FormState) -> str:
    message = (f"<p><strong>{html.escape(shown.error)}</strong></p>"
               if shown.error else "")
    return _page("Connect your Odoo", (
        f"{message}"
        "<p>The assistant will talk to the Odoo instance you name here,"
        " with the key you give it — the same key you would put in a local"
        " configuration.</p>"
        "<form method=\"post\" action=\"/consent\">"
        f"<input type=\"hidden\" name=\"req\""
        f" value=\"{html.escape(shown.req)}\">"
        "<p><label>Odoo URL<br>"
        f"<input type=\"text\" name=\"odoo_url\" required size=\"48\""
        f" value=\"{html.escape(shown.odoo_url)}\"></label></p>"
        "<p><label>API key<br>"
        "<input type=\"password\" name=\"api_key\" required size=\"48\">"
        "</label></p>"
        "<p><label>Database <small>(required on Odoo Online (*.odoo.com):"
        " the name shown at /web/database/selector)</small><br>"
        f"<input type=\"text\" name=\"db\" size=\"48\""
        f" value=\"{html.escape(shown.db)}\"></label></p>"
        "<fieldset><legend>What the assistant may do</legend>"
        "<p><label><input type=\"radio\" name=\"policy\" value=\"read\""
        f"{' checked' if shown.policy == 'read' else ''}>"
        " Read only: the assistant can look, never change</label></p>"
        "<p><label><input type=\"radio\" name=\"policy\""
        " value=\"standard\""
        f"{' checked' if shown.policy == 'standard' else ''}>"
        " Standard: create, update, confirm; never delete, cancel or"
        " mass-mail</label></p>"
        "</fieldset>"
        "<p>Stored: the Odoo address, an encrypted copy of this key, the"
        " policy and the reference documents the assistant generates —"
        " never your records or conversations. See the"
        f" <a href=\"{html.escape(deps.public_url)}/privacy\">privacy"
        " notice</a>.</p>"
        "<p><button type=\"submit\">Connect</button></p></form>"))


def _page(title: str, body: str) -> str:
    return ("<html><head><meta charset=\"utf-8\">"
            f"<title>{html.escape(title)}</title></head><body>"
            f"<h1>{html.escape(title)}</h1>{body}</body></html>")
