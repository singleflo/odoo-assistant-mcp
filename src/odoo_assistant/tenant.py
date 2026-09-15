#!/usr/bin/env python3
"""The one seam that makes the server multi-tenant.

A request arriving over streamable HTTP carries an authenticated subject; the
remote middleware binds a `Tenant` for that request and every tool body reads
it back through `current()` — contextvars reach sync tool bodies there, since
anyio copies the request context into the worker thread. Two things follow the
tenant instead of the process environment:

  * the Odoo client — `odoo_for()` mints one client per subject for the
    process' lifetime, under a lock so concurrent first calls share it;
  * the gate's policy — `server_safety`'s three readers consult `current()`
    FIRST and only fall through to the environment when it is None, so the
    plain stdio server behaves byte-identically to before.

`policy` mirrors the two operator postures the env variables spell: `read`
is `ODOO_MCP_ALLOW=none`, `standard` is the package defaults (`*` with
DEFAULT_DENY). Deletion is never part of either posture — it belongs to a
local install with `ODOO_MCP_ALLOW_UNLINK=yes`.
"""
import sys
import threading
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# Same bootstrap as server.py and server_safety.py: the nine scripts are flat
# modules that import each other by bare name, from the repo and from a wheel.
sys.path.insert(0, str(Path(__file__).parent / "odoo_scripts"))

from odoo_client import Odoo, connect  # noqa: E402  (needs the bootstrap above)


@dataclass(frozen=True)
class Tenant:
    """One authenticated caller's connection to one Odoo instance."""

    subject: str
    base_url: str
    api_key: str
    db: str
    policy: Literal["read", "standard"]

    def __post_init__(self) -> None:
        if self.policy not in ("read", "standard"):
            raise ValueError(
                f"Tenant policy must be 'read' or 'standard', "
                f"got {self.policy!r}.")


_current: ContextVar[Tenant | None] = ContextVar("odoo_tenant", default=None)


def current() -> Tenant | None:
    """The tenant bound for this request — None on the plain env path."""
    return _current.get()


def bind(tenant: Tenant) -> Token:
    """Bind for the current request context; hand back the token for `reset`."""
    return _current.set(tenant)


def reset(token: Token) -> None:
    """Undo one `bind` — the request's context, not the process, forgets it."""
    _current.reset(token)


_clients: dict[str, Odoo] = {}
_clients_lock = threading.Lock()


def odoo_for(tenant: Tenant) -> Odoo:
    """The one client for this subject, connected on first use.

    Keyed by subject alone: a subject is one human's consent to one instance,
    so whatever else changes about their tenant record, the live connection
    is reused. The double-checked read outside the lock keeps the common call
    lock-free.
    """
    existing = _clients.get(tenant.subject)
    if existing is not None:
        return existing
    with _clients_lock:
        if tenant.subject not in _clients:
            _clients[tenant.subject] = connect(
                base=tenant.base_url,
                db=tenant.db,
                user="",
                key=tenant.api_key,
            )
        return _clients[tenant.subject]
