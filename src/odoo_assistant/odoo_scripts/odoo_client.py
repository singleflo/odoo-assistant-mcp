#!/usr/bin/env python3
"""Odoo client — single entry point for every call. Stdlib only.

Handles two transports transparently:

  json2    POST /json/2/<model>/<method>   requires the `commons_odoo` module
  xmlrpc   /xmlrpc/2/object                always available on stock Odoo

The caller writes one signature and never thinks about the wire format:

    from odoo_client import connect
    odoo = connect()
    n = odoo.call("crm.lead", "search_count", [[["type", "=", "opportunity"]]])

Credentials come ONLY from the environment — no defaults, ever. A default
pointing at production is how an experiment becomes an incident.

    ODOO_BASE_URL   http://host:8069   (no trailing slash)
    ODOO_API_KEY    Settings > Users > API Keys
    ODOO_DB         database name          (required for xmlrpc)
    ODOO_USER       login, often 'admin'   (required for xmlrpc)
"""
import json
import os
import ssl
import sys
import urllib.request
import urllib.error
import xmlrpc.client

PRODUCTION_HOSTS = ("app.persevida.com",)


def _check_tls():
    """Fail loudly when this interpreter cannot verify TLS certificates.

    On macOS a stock python.org build has no CA bundle until
    `Install Certificates.command` is run. Every HTTPS call then dies with
    CERTIFICATE_VERIFY_FAILED — and the symptoms look like anything BUT a
    Python problem: database discovery returns nothing, so the client reports
    "needs ODOO_DB"; the fallback endpoints fail the same way.

    A cold session hit exactly this: eight attempts against a production
    instance, each failing for a different apparent reason, because `python3`
    resolved to 3.8.10 with no certificates while 3.11 on the same machine
    worked first try. Naming the cause here costs one check; not naming it
    cost eight tool calls and a wrong conclusion.
    """
    try:
        ssl.create_default_context().load_default_certs()
        paths = ssl.get_default_verify_paths()
        if paths.cafile or paths.capath or ssl.create_default_context().cert_store_stats()["x509_ca"]:
            return
    except Exception:
        pass
    sys.stderr.write(
        f"\nThis Python cannot verify TLS certificates, so every https:// "
        f"call will fail.\n"
        f"  interpreter: {sys.executable} ({sys.version.split()[0]})\n\n"
        f"Use an interpreter that has certificates:\n"
        f"  /opt/homebrew/bin/python3 <script> ...\n"
        f"or fix this one:\n"
        f"  /Applications/Python\\ 3.x/Install\\ Certificates.command\n"
        f"  python3 -m pip install --upgrade certifi\n\n")


def _ssl_ctx():
    """Verified TLS, using certifi when the interpreter has no CA bundle.

    On macOS the system python3 (3.8.x from python.org) ships without CA
    certificates. Every HTTPS call dies with CERTIFICATE_VERIFY_FAILED, and
    the symptoms cascade: db discovery returns nothing, so the agent reports
    "needs ODOO_DB"; API calls fail with misleading errors. certifi fixes
    it — but it may live in a different interpreter's site-packages (the
    Hermes venv at 3.11 has it; this 3.8 does not). Search there too.
    """
    try:
        ctx = ssl.create_default_context()
        if ctx.cert_store_stats()["x509_ca"] > 0:
            return ctx
    except Exception:
        pass
    # Try certifi from the current interpreter first
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    # Fall back to known venv locations on this machine
    import glob
    for pattern in (
            "/Users/crotti/.hermes/hermes-agent/venv/lib/*/site-packages",
            os.path.expanduser("~/.hermes/hermes-agent/venv/lib/*/site-packages"),
            "/opt/homebrew/lib/python3.*/site-packages",
    ):
        for sp in glob.glob(pattern):
            if sp not in sys.path:
                sys.path.insert(0, sp)
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    # Last resort: unverified context (better than crashing)
    return ssl.create_default_context()


class OdooError(RuntimeError):
    """Odoo refused the call. Carries the server message, and is CATCHABLE —
    unlike the legacy client that called sys.exit(1) on HTTP errors, which
    made `try/except` useless and killed the process instead."""


class OdooExecutedButUnserializable(RuntimeError):
    """The method RAN, but Odoo could not serialise its return value.

    Odoo's XML-RPC endpoint calls `dumps((result,))` without `allow_none`, so
    a method returning None raises *after* the work is committed. Observed
    with `account.payment.action_post`: the call raised, and the payment was
    in state `in_process` — done.

    Treating this as a failure is the dangerous mistake: a retry would post
    the payment twice. The only correct response is to RE-READ the record and
    decide from its actual state.
    """


class MissingCredentials(RuntimeError):
    pass


class ProductionWriteBlocked(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not follow redirects.

    Without this, a 303 to /web/login is followed and urlopen returns 200
    with the login page's HTML — which looks like success and then explodes
    on json.loads. That is exactly how the transport auto-detection silently
    picked the wrong transport."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _xmlrpc_proxy(url, **kw):
    """ServerProxy that verifies TLS with a working CA bundle.

    xmlrpc.client builds its own HTTPS connection and ignores the opener used
    for the JSON transport, so without an explicit context it inherits the
    interpreter's empty trust store and every call to an https:// instance
    dies with CERTIFICATE_VERIFY_FAILED.
    """
    if url.startswith("https://"):
        kw["context"] = _ssl_ctx()
    return xmlrpc.client.ServerProxy(url, **kw)


class Odoo:
    def __init__(self, base_url, api_key, db=None, user=None, transport=None):
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.db = db
        self.user = user
        self.uid = None
        self._models = None
        self.transport = transport or self._detect_transport()
        if self.transport == "xmlrpc":
            self._xmlrpc_login()

    # ---------------------------------------------------------------- setup
    def _detect_transport(self):
        """Try /json/2 first (richer, fewer round-trips). Fall back to
        XML-RPC, which every Odoo has. A 303 redirect to /web/login means
        the module is gone and Bearer auth is no longer honoured."""
        try:
            req = urllib.request.Request(
                f"{self.base}/json/2/res.users/search_count",
                data=json.dumps({"args": [[]]}).encode(),
                method="POST",
                headers={"Authorization": f"Bearer {self.key}",
                         "Content-Type": "application/json"},
            )
            with _opener.open(req, timeout=15, context=_ssl_ctx()) as r:
                if r.status == 200:
                    return "json2"
        except Exception:
            pass
        return "xmlrpc"

    def _xmlrpc_login(self):
        """Authenticate. The login is OPTIONAL — the API key identifies its
        owner on its own.

        An Odoo API key belongs to exactly one user (`res.users.apikeys`), and
        `execute_kw` accepts it only with that user's uid — every other uid
        returns `Access Denied`. So the uid can be DISCOVERED instead of
        guessed, and `ODOO_USER` becomes unnecessary.

        This matters because `authenticate()` returns **False** for a wrong
        login instead of raising. A guessed login produces silence, not an
        error, and an agent reads that silence as "permission problem" while
        the real cause is a made-up username. A cold-start session guessed
        'admin' and happened to be right; on an instance where the login is
        an email address it would have failed with no idea why.
        """
        if not self.db:
            raise MissingCredentials(
                "XML-RPC transport needs ODOO_DB.\n"
                "Databases: python3 -c \"import xmlrpc.client as x; "
                "print(x.ServerProxy('%s/xmlrpc/db').list())\"" % self.base
            )
        common = _xmlrpc_proxy(f"{self.base}/xmlrpc/2/common")
        self._models = _xmlrpc_proxy(
            f"{self.base}/xmlrpc/2/object", allow_none=True)

        if self.user:
            self.uid = common.authenticate(self.db, self.user, self.key, {})
            if not self.uid:
                # Do not stop here: the login may simply be wrong while the
                # key is fine. Fall through to discovery and report both.
                found = self._discover_uid()
                if found:
                    self.uid = found
                    self.user_guess_failed = True
                    return
                raise OdooError(
                    f"Authentication failed for login '{self.user}' on db "
                    f"'{self.db}', and the key does not resolve to a user "
                    f"either. Check that the API key belongs to THIS database."
                )
            return

        # No login given: find the key's owner.
        self.uid = self._discover_uid()
        if not self.uid:
            raise MissingCredentials(
                "Could not resolve the API key to a user.\n"
                "Either the key does not belong to db '%s', or the instance "
                "has an unusually high uid.\n"
                "Set ODOO_USER to the login (NOT the email) to authenticate "
                "explicitly." % self.db
            )

    def _discover_uid(self):
        """Find which user this API key belongs to, without a login.

        `execute_kw(db, uid, key, ...)` succeeds only when uid is the key's
        owner; anything else raises Access Denied. Odoo assigns low uids to
        real users (1 is the system/root user), so a short scan finds it.
        Verified on a live instance: uid=2 accepted, uids 1/3/7 denied.
        """
        for uid in range(1, 60):
            try:
                rows = self._models.execute_kw(
                    self.db, uid, self.key, "res.users", "read",
                    [[uid], ["login"]], {})
            except Exception:
                continue
            if rows:
                self.user = rows[0].get("login") or self.user
                return uid
        return None

    # ----------------------------------------------------------------- call
    def call(self, model, method, args=None, kwargs=None):
        """One signature for both transports.

        args    positional arguments, e.g. [[domain]] or [[ids], {vals}]
        kwargs  keyword arguments, e.g. {"fields": [...], "limit": 10,
                                         "context": {...}}
        """
        args = args if args is not None else []
        kwargs = kwargs or {}
        if method.startswith("_"):
            raise OdooError(
                f"{model}.{method}: private methods are always rejected by "
                "Odoo (check_method_name). Use the public wizard instead."
            )
        if self.transport == "xmlrpc":
            return self._call_xmlrpc(model, method, args, kwargs)
        return self._call_json2(model, method, args, kwargs)

    def _call_xmlrpc(self, model, method, args, kwargs):
        try:
            return self._models.execute_kw(
                self.db, self.uid, self.key, model, method, args, kwargs)
        except xmlrpc.client.Fault as f:
            msg = f.faultString.strip()
            # Odoo serialises the RESULT with dumps() and no allow_none, so a
            # method returning None blows up AFTER doing its work. Same for
            # methods returning a RECORDSET (message_post returns mail.message):
            # XML-RPC has no type for it. Both mean "executed, unserialisable".
            executed = (
                ("cannot marshal None" in msg and "dumps" in msg)
                or ("KeyError: <class 'odoo.api." in msg)
                or ("cannot marshal" in msg and "dumps" in msg)
            )
            if executed:
                raise OdooExecutedButUnserializable(
                    f"{model}.{method} EXECUTED, but Odoo could not serialise "
                    f"its return value.\n"
                    f"The change IS applied. Do NOT retry — re-read the record "
                    f"and report its actual state."
                ) from None
            # Extract the actual error message from Odoo's traceback.
            # The full fault string is a Python traceback; the useful line is
            # the last non-empty line (the Odoo error). Keep up to 1200 chars
            # so field names and validation messages are not truncated —
            # a 400-char cap once hid "lost_reason" and caused 3 retries.
            lines = [l.strip() for l in msg.strip().split("\n") if l.strip()]
            short = lines[-1] if lines else msg[:200]
            if len(short) < 20 and len(lines) > 1:
                short = lines[-2] + " — " + lines[-1]
            raise OdooError(f"{model}.{method}: {short[:1200]}") from None

    def _call_json2(self, model, method, args, kwargs):
        # /json/2 quirks: record ids go under "ids" (NOT as first positional),
        # and keyword arguments are FLAT in the body, not nested under kwargs.
        payload = {"args": args}
        payload.update(kwargs)
        req = urllib.request.Request(
            f"{self.base}/json/2/{model}/{method}",
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Authorization": f"Bearer {self.key}",
                     "Content-Type": "application/json"},
        )
        try:
            with _opener.open(req, timeout=120, context=_ssl_ctx()) as r:
                body = r.read().decode()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            raise OdooError(
                f"{model}.{method}: HTTP {e.code} {e.read().decode()[:300]}") from None

    # ------------------------------------------------------------ shortcuts
    def search_count(self, model, domain, context=None):
        return self.call(model, "search_count", [domain],
                         {"context": context} if context else {})

    def search_read(self, model, domain, fields, limit=None, order=None, context=None):
        kw = {"fields": fields}
        if limit:
            kw["limit"] = limit
        if order:
            kw["order"] = order
        if context:
            kw["context"] = context
        return self.call(model, "search_read", [domain], kw)

    def read_group(self, model, domain, fields, groupby, context=None):
        kw = {"context": context} if context else {}
        return self.call(model, "read_group", [domain, fields, groupby], kw)

    def fields_get(self, model, fields=None, attributes=None):
        return self.call(model, "fields_get", [fields or []],
                         {"attributes": attributes or ["string", "type", "selection"]})

    def info(self):
        common = _xmlrpc_proxy(f"{self.base}/xmlrpc/2/common")
        v = common.version()
        sv = str(v.get("server_version", ""))
        return {
            "base_url": self.base,
            "database": self.db,
            "uid": self.uid,
            "transport": self.transport,
            "odoo_version": v.get("server_serie"),
            "edition": "enterprise" if "+e" in sv else "community",
        }


def _is_production(url):
    return any(h in url for h in PRODUCTION_HOSTS)


def add_connection_args(parser):
    """Attach --url/--key/--db/--user to any argparse parser.

    Credentials should be passable per call, not only through the
    environment: an MSP works on several instances in one session, and
    baking one instance into ~/.hermes/.env makes the skill lie about which
    system it is talking to.
    """
    g = parser.add_argument_group("connection (overrides the environment)")
    g.add_argument("--url", help="Odoo base URL, no trailing slash")
    g.add_argument("--key", help="API key (never a password)")
    g.add_argument("--db", help="database name; auto-discovered when omitted")
    g.add_argument("--user", help="login; almost never needed — the key "
                                  "identifies its own owner")
    return parser


def connect_from_args(args=None, allow_write=False):
    """Build a client from CLI arguments, falling back to the environment.

    Precedence: explicit argument > environment > discovery.
    """
    return connect(
        allow_write=allow_write,
        base=getattr(args, "url", None),
        key=getattr(args, "key", None),
        db=getattr(args, "db", None),
        user=getattr(args, "user", None),
    )


def discover_db(base):
    """Ask the server which databases it serves.

    Returns the name when there is exactly one — the common case — so
    ODOO_DB does not have to be supplied.

    Falls back to `/web/session/get_session_info`, which reports the database
    even when `list_db = False` hides the XML-RPC list. Production instances
    almost always hide it, and without this fallback the only remaining option
    is to guess the name — which produced eight failed attempts in one cold
    session before the agent gave up and asked.
    """
    try:
        db = _xmlrpc_proxy(f"{base}/xmlrpc/db", allow_none=True)
        names = db.list()
        if isinstance(names, list) and len(names) == 1:
            return names[0]
    except Exception:
        pass

    # list_db is off (normal in production): the session endpoint still tells
    # us, without any credentials.
    try:
        req = urllib.request.Request(
            f"{base}/web/session/get_session_info",
            data=json.dumps({"jsonrpc": "2.0", "method": "call",
                             "params": {}}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST")
        with _opener.open(req, timeout=15, context=_ssl_ctx()) as r:
            body = json.loads(r.read().decode())
        name = (body.get("result") or {}).get("db")
        if name:
            return name
    except Exception:
        pass
    return None


def connect_cli(allow_write=False):
    """Connect, taking --url/--key/--db/--user off sys.argv if present.

    Every script in this skill has its own argument parsing, so instead of
    rewriting each one, this STRIPS the connection flags from sys.argv before
    the script reads the rest. The result: credentials can be passed per call
    on any script, without changing how it handles its own arguments.

        python3 scripts/query.py crm --url http://host:8069 --key KEY

    Precedence: flag > environment > discovery. Nothing is ever guessed.
    """
    argv, taken = [], {}
    i = 0
    flags = {"--url": "base", "--key": "key", "--db": "db", "--user": "user"}
    while i < len(sys.argv):
        a = sys.argv[i]
        if a in flags and i + 1 < len(sys.argv):
            taken[flags[a]] = sys.argv[i + 1]
            i += 2
            continue
        if "=" in a and a.split("=", 1)[0] in flags:
            k, v = a.split("=", 1)
            taken[flags[k]] = v
            i += 1
            continue
        argv.append(a)
        i += 1
    sys.argv = argv
    return connect(allow_write=allow_write, **taken)


def connect(allow_write=False, base=None, key=None, db=None, user=None):
    """Build a client. Explicit arguments win over the environment.

    Only the URL and the API key are genuinely required:
      - the database is discovered when the server exposes its list,
      - the login is resolved from the key itself.
    """
    base = base or os.environ.get("ODOO_BASE_URL")
    key = key or os.environ.get("ODOO_API_KEY")
    db = db or os.environ.get("ODOO_DB")
    user = user or os.environ.get("ODOO_USER")

    missing = [n for n, v in (("URL", base), ("API key", key)) if not v]
    if missing:
        raise MissingCredentials(
            "Missing: " + ", ".join(missing) + "\n"
            "There is no default — the script will not guess an instance.\n\n"
            "Pass them on the command line:\n"
            "  python3 scripts/query.py --url http://host:8069 --key KEY\n\n"
            "or export them for the session:\n"
            "  export ODOO_BASE_URL='http://host:8069'\n"
            "  export ODOO_API_KEY='...'   # Preferences > Account Security\n\n"
            "The database is auto-discovered and the login comes from the\n"
            "key, so --db and --user are rarely needed.\n"
            "Ask the user for an API KEY, never a password.\n"
        )
    if not db:
        if base.startswith("https://"):
            _check_tls()          # names the real cause before it looks like
                                  # a missing-database problem
        db = discover_db(base)
    if allow_write and _is_production(base):
        if os.environ.get("ODOO_ALLOW_PROD_WRITE", "").lower() not in ("yes", "true", "1"):
            raise ProductionWriteBlocked(
                f"Write operation on PRODUCTION ({base}) blocked.\n"
                "Set ODOO_ALLOW_PROD_WRITE=yes only if that is truly intended."
            )
    return Odoo(base, key, db, user)


if __name__ == "__main__":
    import argparse
    ap = add_connection_args(argparse.ArgumentParser(
        description="Show which instance and which user this key reaches."))
    a = ap.parse_args()
    try:
        odoo = connect_from_args(a)
    except (MissingCredentials, ProductionWriteBlocked) as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        raise SystemExit(2)
    for k, v in odoo.info().items():
        print(f"  {k:16s} {v}")
    # Make the identity explicit: a session once guessed the login, got lucky,
    # and reported success. Showing WHO the key belongs to makes a wrong
    # assumption visible instead of silent.
    supplied = a.user or os.environ.get("ODOO_USER")
    print(f"  {'login':16s} {odoo.user}"
          f"{'' if supplied else '  (resolved from the API key, not supplied)'}")
    if getattr(odoo, "user_guess_failed", False):
        print(f"\n  WARNING: login '{supplied}' did not authenticate.\n"
              f"  The key actually belongs to '{odoo.user}' (uid={odoo.uid}). "
              f"Drop the --user/ODOO_USER override.", file=sys.stderr)


# ----------------------------------------------------------- helpers
# Many Odoo fields are many2one: the value is either [id, "name"] or False.
# Accessing [1] on False is the most common crash in agent-written scripts.
# This helper makes the pattern safe without a try/except at every call site.

def m2o(value, index=1, default="—"):
    """Safely read a many2one tuple.

    >>> m2o([42, "ACME"])
    'ACME'
    >>> m2o(False)
    '—'
    >>> m2o(None)
    '—'
    >>> m2o([42, "ACME"], index=0)
    42
    """
    if isinstance(value, (list, tuple)) and len(value) > index:
        return value[index]
    return default
