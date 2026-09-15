#!/usr/bin/env bash
# Plain-HTTP transcript of the deployed hosted endpoint (no Odoo, no token).
# Usage: ./scripts/remote_live_check.sh [URL]
#   URL defaults to $ODOO_REMOTE_PUBLIC_URL, then https://mcp.singleflo.com.
# The three checks a freshly deployed endpoint must answer before any OAuth
# or MCP traffic is worth attempting. Each prints PASS or FAIL; the exit
# status is nonzero when any check fails.
set -euo pipefail

BASE="${1:-${ODOO_REMOTE_PUBLIC_URL:-https://mcp.singleflo.com}}"
fail=0

echo "== GET $BASE/health =="
if curl -fsS --max-time 30 "$BASE/health"; then
  echo
  echo "PASS: /health answers 200"
else
  echo
  echo "FAIL: /health did not answer 200"
  fail=1
fi
echo

echo "== GET $BASE/.well-known/oauth-protected-resource/mcp =="
if prm=$(curl -fsS --max-time 30 "$BASE/.well-known/oauth-protected-resource/mcp"); then
  echo "$prm"
  echo "PASS: protected-resource metadata answers 200"
else
  echo "FAIL: protected-resource metadata did not answer 200"
  fail=1
fi
echo

echo "== POST $BASE/mcp without bearer (expect 401 + WWW-Authenticate) =="
headers=$(curl -sS --max-time 30 -o /dev/null -D - -X POST "$BASE/mcp" \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' | tr -d '\r') || true
echo "$headers" | grep -Ei '^HTTP/|^www-authenticate:' || true
if echo "$headers" | grep -q '^HTTP/.* 401' \
   && echo "$headers" | grep -qi '^www-authenticate:.*resource_metadata='; then
  echo "PASS: /mcp answers 401 naming the resource metadata"
else
  echo "FAIL: /mcp did not answer 401 with resource_metadata"
  fail=1
fi

exit "$fail"
