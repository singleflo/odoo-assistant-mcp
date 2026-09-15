#!/usr/bin/env python3
"""Mint an access token from a hosted odoo-assistant server.

Drives the whole flow a client walks: dynamic client registration, PKCE,
/authorize, the consent page (this is the step that stores YOUR Odoo
credentials as a tenant), and the code exchange. Prints the access token on
stdout, ready for an Authorization: Bearer header.

The server URL comes from ODOO_REMOTE_PUBLIC_URL (--url overrides). The Odoo
side of the consent form comes from ODOO_REMOTE_TEST_ODOO_URL,
ODOO_REMOTE_TEST_API_KEY and ODOO_REMOTE_TEST_DB. The redirect URI points at
port 1 on purpose: the code is exchanged directly, no local listener exists.

Used by humans for quick checks and by the deployed E2E (remote_live tests).
"""
import argparse
import base64
import hashlib
import os
import secrets
import sys
from urllib.parse import parse_qs, urlparse

import httpx

REDIRECT_URI = "http://localhost:1/callback"


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        sys.exit(f"{name} is required")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Register a client, consent with your Odoo credentials,"
                    " and print the resulting OAuth access token.")
    parser.add_argument(
        "--url", default=os.environ.get("ODOO_REMOTE_PUBLIC_URL"),
        help="base URL of the hosted server (default: $ODOO_REMOTE_PUBLIC_URL)")
    parser.add_argument(
        "--policy", default="read", choices=["read", "standard"],
        help="what the assistant may do on your instance (default: read)")
    args = parser.parse_args()
    if not args.url:
        parser.error("no server URL: pass --url or set ODOO_REMOTE_PUBLIC_URL")
    base = args.url.rstrip("/")

    odoo_url = _env("ODOO_REMOTE_TEST_ODOO_URL").rstrip("/")
    api_key = _env("ODOO_REMOTE_TEST_API_KEY")
    db = _env("ODOO_REMOTE_TEST_DB")

    http = httpx.Client(base_url=base, follow_redirects=False, timeout=30)

    registered = http.post("/register", json={
        "redirect_uris": [REDIRECT_URI],
        "client_name": "remote_token",
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    })
    registered.raise_for_status()
    client_id = registered.json()["client_id"]

    # /authorize and /consent answer 302 on success. raise_for_status() would
    # REJECT those: httpx (>= 0.28) raises on every redirect response when
    # follow_redirects=False. Assert the redirect explicitly instead.

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")

    asked = http.get("/authorize", params={
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "remote-token",
        "scope": "odoo",
        "resource": f"{base}/mcp",
    })
    if asked.status_code != 302:
        sys.exit(f"/authorize answered {asked.status_code}: {asked.text[:200]}")
    req = parse_qs(urlparse(asked.headers["location"]).query)["req"][0]

    consented = http.post("/consent", data={
        "req": req, "odoo_url": odoo_url, "api_key": api_key,
        "db": db, "policy": args.policy,
    })
    if consented.status_code != 302:
        # 200 = the consent form was re-rendered with an error (bad
        # credentials, unreachable Odoo); anything else is a protocol fault.
        sys.exit(f"consent failed ({consented.status_code}): {consented.text[:300]}")
    code = parse_qs(urlparse(consented.headers["location"]).query)["code"][0]

    token = http.post("/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    })
    token.raise_for_status()
    print(token.json()["access_token"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
