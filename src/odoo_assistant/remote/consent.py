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
import ast
import html
import ipaddress
import logging
import multiprocessing
import re
import secrets
import socket
import sys
from dataclasses import dataclass, replace
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import parse_qs, urlsplit

from anyio.to_thread import run_sync as run_in_thread
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from odoo_assistant.remote import ui
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
    """The two auth-provider methods consent needs (todo 6 owns the class)."""

    def complete_consent(self, pending_id: str, subject: str) -> str: ...

    def refuse_consent(self, pending_id: str) -> str: ...


@dataclass(frozen=True, slots=True)
class ConsentDeps:
    """The handlers' whole world, attached to `app.state.consent_deps`."""

    store: Store
    provider: ConsentProvider
    public_url: str
    allow_private_targets: bool
    publisher: str = "the odoo-assistant maintainers"


@dataclass(frozen=True, slots=True)
class _FormState:
    """What the form shows on a (re)render: the pending id, who is asking,
    the previous submission minus the key, and the error explaining the
    re-render."""

    req: str
    client_name: str = ""
    redirect_uri: str = ""
    scopes: str = ""
    odoo_url: str = ""
    db: str = ""
    login: str = ""
    policy: str = "read"
    error: str | None = None


async def consent_form(request: Request) -> Response:
    """GET /consent?req=<id>: the page a user fills in with their own Odoo."""
    deps: ConsentDeps = request.app.state.consent_deps
    pending = _peek_pending(deps.store, request.query_params.get("req", ""))
    if pending is None:
        return _plain_page(_GONE_MESSAGE, status_code=400)
    return HTMLResponse(_form_html(deps, _asking(deps, pending)))


def _asking(deps: ConsentDeps, pending: PendingAuthz) -> _FormState:
    """A blank form that says who is asking, for what, and where it leads.

    The specification's consent-UI rules (MCP 2026-07-28, security best
    practices) require the page to name the requesting client, state the
    scope and show the redirect it registered — a consent screen that omits
    them asks a user to approve a stranger. The name arrives from the
    client's own dynamic registration, so it is untrusted text like any
    other: escaped on the way out, and shown next to the redirect URI, which
    is the part an attacker cannot fake past `redirect_uri` validation.
    """
    client = deps.store.get_client(pending.client_id)
    name = getattr(client, "client_name", None) or pending.client_id
    return _FormState(req=pending.id, client_name=name,
                      redirect_uri=pending.redirect_uri,
                      scopes=pending.scopes or "odoo")


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

    if fields.get("action") == "deny":
        # Refusing is part of the flow, not the absence of one. The client
        # is told `access_denied` and the pending row is consumed, so the
        # browser goes back to the assistant that asked instead of being
        # left on a page whose only exit is the window's close button.
        logger.info("consent: refused for client %s", pending.client_id)
        return RedirectResponse(deps.provider.refuse_consent(pending.id),
                                status_code=302)

    odoo_url = fields.get("odoo_url", "").strip().rstrip("/")
    api_key = fields.get("api_key", "")
    db = fields.get("db", "").strip()
    login = fields.get("login", "").strip()
    policy = fields.get("policy", "")
    shown = replace(_asking(deps, pending),
                    odoo_url=odoo_url, db=db, login=login, policy="read")
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
        outcome = await run_in_thread(
            _verify_isolated, odoo_url, db, api_key, login)
    except Exception as exc:
        logger.info("consent: verification failed for host %s", host)
        return HTMLResponse(_form_html(deps, replace(
            shown, error=str(exc) or "The Odoo server refused the"
            " connection.")))
    match outcome.status:
        case "timeout":
            logger.info("consent: no answer from host %s", host)
            return HTMLResponse(_form_html(
                deps, replace(shown, error=_readable_failure(outcome.detail))))
        case "error":
            logger.info("consent: verification failed for host %s", host)
            return HTMLResponse(_form_html(deps, replace(
                shown, error=_readable_failure(outcome.detail))))
        case "ok":
            # Odoo's own answer beats the typed one: a wrong login is not an
            # error while the probe can still rescue it, so keeping what was
            # submitted would store a value Odoo refuses.
            login = outcome.login or login

    existing = deps.store.find_tenant_by_key_hash(key_hash(odoo_url, api_key))
    if existing is not None:
        subject = existing.subject
        deps.store.put_tenant(
            replace(existing, db=db, policy=policy, login=login))
    else:
        subject = "t_" + secrets.token_urlsafe(16)
        deps.store.put_tenant(Tenant(subject=subject, base_url=odoo_url,
                                     api_key=api_key, db=db, policy=policy,
                                     login=login))
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


def _verify_credentials(odoo_url: str, db: str, api_key: str,
                        login: str = "") -> str:
    """Prove the key works: connect (the JSON-2 path authenticates with the
    key alone), then read the key owner's login.

    `login` is what rescues the XML-RPC path. There, the uid is a parameter of
    the protocol — `service/model.py` reads it straight out of the call and
    `res.users.check` builds the credential from *that* user's login — so a
    caller holding only a key cannot ask Odoo who owns it and the client falls
    back to probing uid 1 to 59. Given the login, one
    `common.authenticate(db, login, key)` settles it, at any uid.

    Returns the login Odoo itself reports for the connected user, which is
    the one worth keeping: a wrong one submitted here is not an error while
    the probe can still rescue it, so storing what was typed would persist a
    value Odoo refuses and pay for a failed `authenticate` on every later
    connection. Reading it back also means a tenant that connected by probe
    never has to probe again.
    """
    odoo = connect(base=odoo_url, db=db, user=login, key=api_key)
    rows = odoo.call("res.users", "read", [[odoo.uid], ["login"]], {})
    resolved = rows[0].get("login") if rows else None
    return resolved or getattr(odoo, "user", "") or login


# What the client raises when the uid probe ran out of numbers, and when a
# supplied login was refused. Matched as substrings of the child's exception
# repr, because `odoo_scripts/` is canonical and its wording is not ours to
# restructure into a typed field.
_PROBE_EXHAUSTED = "Could not resolve the API key to a user"
_LOGIN_REFUSED = "Authentication failed for login"
_DB_MISSING = "XML-RPC transport needs ODOO_DB"
_DB_AMBIGUOUS = "so ODOO_DB must name"


def _readable_failure(detail: str) -> str:
    """One sentence a person can act on, from a child process' exception repr.

    The repr is what crossed the pipe, so the raw text reaches the page as
    `MissingCredentials("...\\nSet ODOO_USER to...")` — a class name, literal
    backslash-n, and an instruction to set an environment variable that
    nobody signing in through a browser has anywhere to put.

    The client's own wording is written for someone editing a host config, so
    the cases that name a variable are answered with the field on this page
    that carries the same value. Everything else passes through as written:
    guessing at an unfamiliar failure would hide it.
    """
    unwrapped = _unwrap(detail)
    if _PROBE_EXHAUSTED in unwrapped:
        return ("We could not work out which user this API key belongs to."
                " Fill in the Odoo login below — the address you sign in"
                " with — and try again.")
    if _LOGIN_REFUSED in unwrapped:
        return ("Odoo refused that login and key together. Check the login is"
                " the one you sign in with, and that the key was created in"
                " this same database.")
    if _DB_MISSING in unwrapped:
        return ("We could not work out the database name. Fill in the"
                " Database field below and try again.")
    if _DB_AMBIGUOUS in unwrapped:
        # This one already names the candidates, which is the useful half —
        # only the variable has to become the field.
        named = unwrapped.split(" Set ODOO_DB")[0].replace(
            "ODOO_DB must name one", "the Database field below must name one")
        return named + " Fill it in and try again."
    return unwrapped or "The Odoo server refused the connection."


def _unwrap(detail: str) -> str:
    """`ClassName("text")` back to text; anything else through untouched."""
    match = re.fullmatch(r"\w+\((.*)\)", detail.strip(), re.S)
    if match is None:
        return detail
    try:
        inner = ast.literal_eval(match.group(1))
    except (ValueError, SyntaxError):
        return detail
    return inner if isinstance(inner, str) else detail


@dataclass(frozen=True, slots=True)
class _Verified:
    """What one isolated verification concluded. The process travels along
    so a test (or a supervisor) can confirm nothing survives the call."""

    status: Literal["ok", "error", "timeout"]
    detail: str
    process: BaseProcess | None
    # The login Odoo reported for the connected user; empty unless status is
    # "ok". It is what gets stored, in place of whatever was typed.
    login: str = ""


def _verify_entry(odoo_url: str, db: str, api_key: str, login: str,
                  send_conn: Connection) -> None:
    """Child side: one verification, then the outcome on the pipe — "ok" with
    the login Odoo reported, or "error" with the exception repr. `connect`
    resolves through this module, which the
    spawn interpreter imports fresh; nothing unpicklable crosses, only the
    four strings and the pipe."""
    try:
        send_conn.send(("ok", _verify_credentials(odoo_url, db, api_key, login)))
    except Exception as exc:
        send_conn.send(("error", repr(exc)))
    finally:
        send_conn.close()


_last_verifier: BaseProcess | None = None


def _verify_isolated(odoo_url: str, db: str, api_key: str,
                     login: str = "") -> _Verified:
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
                       args=(odoo_url, db, api_key, login, send_conn),
                       daemon=True)
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
            status, payload = recv_conn.recv()
        except EOFError:  # the child died without answering
            return _Verified("error", "", proc)
        if status == "ok":
            return _Verified(status, "", proc, payload)
        return _Verified(status, payload, proc)
    finally:
        recv_conn.close()


def _reap(proc: BaseProcess) -> None:
    """Terminate, then kill: a hung socket read must never outlive the
    request that started it."""
    proc.terminate()
    proc.join(_TERMINATE_GRACE)
    if proc.is_alive():
        proc.kill()
        proc.join(_TERMINATE_GRACE)


def _plain_page(message: str, status_code: int) -> HTMLResponse:
    return HTMLResponse(
        ui.layout("This request has expired",
                  f"<p>{html.escape(message)}</p>",
                  publisher=_PUBLISHER_FALLBACK),
        status_code=status_code)


# The keyboard hygiene every credential field needs on a phone: iOS
# capitalises the first letter of a URL and autocorrects an API key unless
# told not to, and the consent page is reached from the in-app browser of
# Claude and ChatGPT far more often than from a desktop.
_NO_TYPING_HELP = ('autocapitalize="off" autocorrect="off"'
                   ' spellcheck="false"')
_PUBLISHER_FALLBACK = "the odoo-assistant maintainers"


def _form_html(deps: ConsentDeps, shown: _FormState) -> str:
    error = (f'<p class="error" role="alert">{html.escape(shown.error)}</p>'
             if shown.error else "")
    read_checked = " checked" if shown.policy == "read" else ""
    standard_checked = " checked" if shown.policy == "standard" else ""
    privacy = f"{html.escape(deps.public_url)}/privacy"
    return ui.layout("Connect your Odoo", (
        "<p class=\"lead\">"
        f"<strong>{html.escape(shown.client_name)}</strong> is asking to"
        " reach an Odoo instance on your behalf. Name the instance, give it"
        " a key you created, and choose what it may do.</p>"

        "<dl class=\"facts\">"
        "<div><dt>Requested by</dt>"
        f"<dd>{html.escape(shown.client_name)}</dd></div>"
        "<div><dt>Access returns to</dt>"
        f"<dd><code>{html.escape(shown.redirect_uri)}</code></dd></div>"
        "<div><dt>Scope</dt>"
        f"<dd><code>{html.escape(shown.scopes)}</code></dd></div>"
        "</dl>"

        f"{error}"
        "<form method=\"post\" action=\"/consent\" class=\"consent-form\">"
        f"<input type=\"hidden\" name=\"req\""
        f" value=\"{html.escape(shown.req)}\">"

        "<p class=\"field\">"
        "<label for=\"odoo_url\">Odoo address</label>"
        "<input id=\"odoo_url\" name=\"odoo_url\" type=\"url\" required"
        f" inputmode=\"url\" autocomplete=\"url\" {_NO_TYPING_HELP}"
        " placeholder=\"https://mycompany.odoo.com\""
        f" value=\"{html.escape(shown.odoo_url)}\">"
        "<span class=\"field-help\">The address you open Odoo at. It must be"
        " reachable over https.</span></p>"

        "<p class=\"field\">"
        "<label for=\"api_key\">API key</label>"
        "<input id=\"api_key\" name=\"api_key\" type=\"password\" required"
        f" autocomplete=\"off\" {_NO_TYPING_HELP}>"
        "<span class=\"field-help\">An Odoo API key — never your"
        " password.</span></p>"

        "<details class=\"help\">"
        "<summary>Where to generate an API key in Odoo</summary>"
        "<p>Open the avatar menu in Odoo, then <strong>My Profile</strong>"
        " (called Preferences on some versions), the <strong>Account"
        " Security</strong> tab, then <strong>New API Key</strong>. The key"
        " belongs to one user and carries exactly that user's permissions,"
        " and you can revoke it on its own at any time.</p>"
        "<ul class=\"versions\">"
        "<li><strong>Odoo 16</strong> <em>illustrated steps coming"
        " soon</em></li>"
        "<li><strong>Odoo 17</strong> <em>illustrated steps coming"
        " soon</em></li>"
        "<li><strong>Odoo 18</strong> <em>illustrated steps coming"
        " soon</em></li>"
        "<li><strong>Odoo 19</strong> — a description and an expiry date are"
        " required, three months at most. <em>illustrated steps coming"
        " soon</em></li>"
        "</ul></details>"

        "<p class=\"field\">"
        "<label for=\"login\">Odoo login"
        " <span class=\"optional\">optional</span></label>"
        "<input id=\"login\" name=\"login\" type=\"text\""
        f" autocomplete=\"username\" {_NO_TYPING_HELP}"
        " placeholder=\"jane@mycompany.com\""
        f" value=\"{html.escape(shown.login)}\">"
        "<span class=\"field-help\">The address you sign in to Odoo with, for"
        " the user the key belongs to. Leave it empty and we work it out;"
        " fill it in when we report that we could not. Never your"
        " password.</span></p>"

        "<p class=\"field\">"
        "<label for=\"db\">Database <span class=\"optional\">optional</span>"
        "</label>"
        "<input id=\"db\" name=\"db\" type=\"text\""
        f" autocomplete=\"off\" {_NO_TYPING_HELP}"
        f" value=\"{html.escape(shown.db)}\">"
        "<span class=\"field-help\">Required on Odoo Online"
        " (<code>*.odoo.com</code>), where the name is not the subdomain:"
        " find it at <code>/web/database/selector</code>. Leave it empty"
        " elsewhere — it is discovered.</span></p>"

        "<fieldset class=\"policy\">"
        "<legend>What the assistant may do</legend>"
        "<label class=\"policy-option\">"
        f"<input type=\"radio\" name=\"policy\" value=\"read\"{read_checked}>"
        "<span><strong>Read only: the assistant can look, never"
        " change</strong>"
        "<span class=\"policy-detail\">Searches, reads and counts. Every"
        " write is refused.</span></span></label>"
        "<label class=\"policy-option\">"
        "<input type=\"radio\" name=\"policy\" value=\"standard\""
        f"{standard_checked}>"
        "<span><strong>Standard: create, update, confirm; never delete,"
        " cancel or mass-mail</strong>"
        "<span class=\"policy-detail\">Adds creating records, writing"
        " fields, running workflow actions and messaging colleagues.</span>"
        "</span></label>"
        "<p class=\"policy-note\">Either choice can be changed later by"
        " signing in again. Deletion is never available here.</p>"
        "</fieldset>"

        "<section class=\"assurance\">"
        "<h2>About the key you are about to paste</h2>"
        "<ul>"
        "<li>We are not Odoo. This is an independent project, and the"
        " instance stays yours.</li>"
        "<li>It is an API key, never an account password: this server cannot"
        " sign in as you.</li>"
        "<li>The key is stored encrypted and used only to reach the address"
        " you typed above, on your behalf. It is never shared and never used"
        " for anything else.</li>"
        "<li>Revoking that key inside Odoo ends the access immediately,"
        " without going through us.</li>"
        "<li>Your records and your conversations are never stored here."
        f" What is kept is listed in the <a href=\"{privacy}\">privacy"
        " notice</a>.</li>"
        "</ul></section>"

        "<div class=\"actions\">"
        "<button type=\"submit\" class=\"primary\""
        " data-busy-label=\"Connecting…\">Connect</button>"
        "<button type=\"submit\" class=\"secondary\" name=\"action\""
        " value=\"deny\" formnovalidate data-busy-label=\"Refusing…\">Refuse</button>"
        "</div>"
        # Hidden until the form reports itself as sending. What follows the
        # press is a live connection to the user's Odoo, which can take the
        # better part of half a minute on a cold instance; `role=\"status\"`
        # so a screen reader hears it appear rather than only seeing it.
        "<p class=\"sending-note\" role=\"status\">Checking your Odoo and the"
        " key you gave — this can take up to twenty seconds.</p>"
        "</form>"),
        publisher=deps.publisher)
