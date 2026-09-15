"""The live suite against the deployed hosted endpoint (or a tunnel).

Drives the real flow a host walks — dynamic client registration, PKCE,
/authorize, the consent page (the step that stores the test Odoo as a
tenant), the code exchange — by SUBPROCESSING scripts/remote_token.py, the
same script humans run, so the flow logic is reused and never duplicated.
With the minted bearer token it opens real MCP sessions over Streamable HTTP
and asserts the deployed surface: nineteen tools, a safe read on a
nonexistent record, and the read-policy refusal.

Environment:

* Required gate (the module skips without all three):
  ODOO_REMOTE_PUBLIC_URL (normally https://mcp.singleflo.com; a tunnel URL
  while the Coolify deploy is pending), ODOO_REMOTE_TEST_ODOO_URL,
  ODOO_REMOTE_TEST_API_KEY.
* ODOO_REMOTE_TEST_DB — the token script passes it to the consent form, so
  the MCP tests skip without it; the plain-HTTP tests still run.
* ODOO_REMOTE_TEST_POLICY — "read" (default) or "standard". The write
  refusal is asserted only under "read"; under "standard" that test skips,
  because nothing may ever be written. The ONLY write this suite attempts is
  the refused one on res.partner id 0 — a record that does not exist — so a
  gate failure cannot change data.

Failure path: setting ODOO_REMOTE_TEST_API_KEY to the literal sentinel
"wrong-key-on-purpose" runs exactly one test, which proves the consent step
REJECTS the bad credentials and no token is minted.

CI never runs this module: addopts deselects `remote_live` and this suite is
only ever invoked explicitly (`uv run pytest -m remote_live` — a sanctioned
marker spelling, registered in pyproject). Every external call carries a
generous timeout because the target is a network service.

Test Odoo = the dev instance only, never production.
"""
import anyio
import httpx
import importlib.metadata
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TypeVar

import pytest
from mcp import types
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from tests.test_server_registration import EXPECTED_TOOLS

_REQUIRED_ENV = (
    "ODOO_REMOTE_PUBLIC_URL", "ODOO_REMOTE_TEST_ODOO_URL",
    "ODOO_REMOTE_TEST_API_KEY")
_MISSING = [name for name in _REQUIRED_ENV if not os.environ.get(name)]

PUBLIC_URL = os.environ.get("ODOO_REMOTE_PUBLIC_URL", "").rstrip("/")
POLICY = os.environ.get("ODOO_REMOTE_TEST_POLICY", "read")
_WRONG_KEY_SENTINEL = "wrong-key-on-purpose"
_TIMEOUT = 30.0
_TOKEN_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "remote_token.py")
_LOCAL_VERSION = importlib.metadata.version("odoo-assistant")

pytestmark = [
    pytest.mark.remote_live,
    pytest.mark.skipif(
        bool(_MISSING),
        reason="set " + ", ".join(_MISSING) + " to drive the deployed"
               " endpoint (deployed URL or tunnel; dev-instance credentials"
               " only)"),
]

T = TypeVar("T")


# --------------------------------------------------------------- plain HTTP
def test_protected_resource_metadata_names_this_mcp_endpoint():
    r = httpx.get(f"{PUBLIC_URL}/.well-known/oauth-protected-resource/mcp",
                  timeout=_TIMEOUT)
    assert r.status_code == 200
    assert r.json()["resource"].rstrip("/") == f"{PUBLIC_URL}/mcp"


def test_privacy_page_serves():
    r = httpx.get(f"{PUBLIC_URL}/privacy", timeout=_TIMEOUT)
    assert r.status_code == 200


def test_health_reports_the_deployed_version():
    r = httpx.get(f"{PUBLIC_URL}/health", timeout=_TIMEOUT)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] == _LOCAL_VERSION, (
        "the deployment is not running this checkout's version")


def test_bare_post_to_mcp_is_401_naming_the_resource_metadata():
    r = httpx.post(f"{PUBLIC_URL}/mcp", timeout=_TIMEOUT, headers={
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers.get("www-authenticate", "")


# ------------------------------------------------------- the tokened flow
@pytest.fixture(scope="module")
def token() -> str:
    """The access token scripts/remote_token.py mints for the test Odoo."""
    if os.environ.get("ODOO_REMOTE_TEST_API_KEY") == _WRONG_KEY_SENTINEL:
        pytest.skip("wrong-key sentinel run: only the failure-path test"
                    " executes")
    if not os.environ.get("ODOO_REMOTE_TEST_DB"):
        pytest.skip("ODOO_REMOTE_TEST_DB is not set (the consent form needs"
                    " a database name; see scripts/remote_token.py)")
    proc = subprocess.run(
        [sys.executable, str(_TOKEN_SCRIPT), "--policy", POLICY],
        env=os.environ, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, (
        f"scripts/remote_token.py failed:\n{proc.stdout}\n{proc.stderr}")
    minted = proc.stdout.strip()
    assert minted, "the token script printed no access token"
    return minted


@asynccontextmanager
async def _open_session(token: str):
    """One initialized MCP session over the wire, bearer-authenticated."""
    http = create_mcp_http_client(
        headers={"Authorization": f"Bearer {token}"})
    async with http:
        transport = streamable_http_client(
            f"{PUBLIC_URL}/mcp", http_client=http)
        # mode="legacy" runs the initialize handshake on entry — entering the
        # context IS the initialize; a failed handshake raises here.
        async with Client(transport, mode="legacy") as client:
            yield client


def _over_mcp(token: str, action: Callable[[Client], Awaitable[T]]) -> T:
    async def scenario() -> T:
        async with _open_session(token) as client:
            return await action(client)

    return anyio.run(scenario)


def _text_of(result) -> str:
    return "\n".join(block.text for block in result.content
                     if isinstance(block, types.TextContent))


def test_the_deployed_server_exposes_the_nineteen_expected_tools(token):
    async def action(client: Client):
        return {t.name for t in (await client.list_tools()).tools}

    listed = _over_mcp(token, action)
    assert listed == EXPECTED_TOOLS


def test_count_records_on_a_nonexistent_partner_answers_zero(token):
    # id 0 matches nothing: even if every gate failed, nothing could change.
    async def action(client: Client):
        return await client.call_tool("count_records", {
            "model": "res.partner", "domain": [["id", "=", 0]]})

    result = _over_mcp(token, action)
    assert not result.is_error
    assert _text_of(result) == "0"


def test_write_record_is_refused_under_the_read_policy(token):
    if POLICY != "read":
        pytest.skip("the hosted write refusal is asserted only under the"
                    " read policy; nothing is ever written to the test Odoo")

    async def action(client: Client):
        return await client.call_tool("write_record", {
            "model": "res.partner", "record_id": 0,
            "values": {"name": "must never land"}})

    result = _over_mcp(token, action)
    assert result.is_error
    assert "read-only" in _text_of(result)


# ------------------------------------------------------------ failure path
def test_the_wrong_key_sentinel_fails_consent_and_mints_no_token():
    if os.environ.get("ODOO_REMOTE_TEST_API_KEY") != _WRONG_KEY_SENTINEL:
        pytest.skip("set ODOO_REMOTE_TEST_API_KEY=wrong-key-on-purpose to"
                    " exercise the failure path")
    proc = subprocess.run(
        [sys.executable, str(_TOKEN_SCRIPT), "--policy", POLICY],
        env=os.environ, capture_output=True, text=True, timeout=120)
    assert proc.returncode != 0, (
        f"a token was minted from the sentinel key: {proc.stdout!r}")
    assert not proc.stdout.strip(), "no token may be printed on this path"
    # remote_token.py diagnoses a rejected consent with its own "consent
    # failed" exit message — the consent step, not an earlier one, refused.
    assert "consent failed" in proc.stderr, proc.stderr[-2000:]
