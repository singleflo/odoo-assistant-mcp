#!/usr/bin/env bash
# Plain-HTTP transcript of the deployed hosted endpoint (no Odoo, no token).
# Usage: ODOO_REMOTE_PUBLIC_URL=https://mcp.singleflo.com ./scripts/remote_live_check.sh
# The three checks a freshly deployed endpoint must answer before any OAuth
# or MCP traffic is worth attempting.
set -euo pipefail

BASE="${ODOO_REMOTE_PUBLIC_URL:?set ODOO_REMOTE_PUBLIC_URL, e.g. https://mcp.singleflo.com}"

echo "== GET /health =="
curl -fsS --max-time 30 "$BASE/health"
echo

echo "== GET /.well-known/oauth-protected-resource/mcp =="
curl -fsS --max-time 30 "$BASE/.well-known/oauth-protected-resource/mcp"
echo

echo "== POST /mcp without bearer (expect 401 + WWW-Authenticate) =="
curl -sS --max-time 30 -o /dev/null -D - -X POST "$BASE/mcp" \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' | tr -d '\r' | grep -Ei '^HTTP/|^www-authenticate:'
