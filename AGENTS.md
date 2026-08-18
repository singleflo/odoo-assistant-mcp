# AGENTS.md — odoo-assistant-mcp

MCP server (stdio) that wraps 9 verified Odoo scripts as **19 tools + 8
resources**. Shipped and complete; published on PyPI as version 0.1.0 and
installable with `uvx odoo-assistant`.

## Read before coding

- `docs/PRD.md` — the spec: tools (§5B), auth (§6), safety mapping (§7), packaging (§10). Written before the build, so it trails the code: it says 14 tools and two sections are marked superseded. Trust the code first.
- `docs/mcp-spec/` — the ENTIRE MCP 2026-07-28 spec, vendored (144 files). Consult it instead of the web.
- `docs/RELEASE-CHECKLIST.md` — the only work left, and all of it needs a human: PyPI Trusted Publishing, then the tag.
- `docs/json2-source/` — the Odoo module behind `/json/2`. `odoo_client.py` probes that transport first and falls back to XML-RPC, which Odoo 19 deprecates and 22 removes, so this is the reference for the path that becomes primary.
- `references/BUILD-STATE.md` — sanity figures for the dev instance

## Commands

```bash
uv run pytest                       # 223 tests. addopts already excludes live+wheel
uv build && uv run pytest -m wheel  #   3 tests. needs dist/ AND network (uvx)
uv run pytest -m live               #   6 tests. needs the env vars below
```

**Never write `-m "not live"`.** A `-m` on the command line replaces `addopts`
instead of narrowing it, so that spelling silently re-enables the `wheel` tests
and they fail on a clean checkout with no `dist/`. CI spells it in full:
`-m "not live and not wheel"`.

Protocol check: `npx @modelcontextprotocol/inspector` against the server.

## Hard rules

- **Never rewrite `src/odoo_assistant/odoo_scripts/`.** Those 9 scripts are verified against a live instance and are the source of truth; the server is a thin wrapper importing them. They came from an agent skill that has been retired, so this repo is the canonical copy — `scripts/sync-from-skill.sh` has no upstream left and exits 1. Fix a bug there only with a live test proving it; otherwise adapt the wrapper.
- Scripts are **flat modules**: each does `sys.path.insert(0, <own dir>)` + `from odoo_client import ...`. Keep that import style working when packaging.
- SDK: `from mcp.server import MCPServer` (SDK 2.0, pinned `mcp[cli]>=2.0.0,<3`). `MCPServer(...)` defaults `version` to `""` — pass it explicitly or every host displays an empty version.
- **Module decomposition**: `server.py` stays thin. Tools and resources live in `tools_read.py` / `tools_write.py` / `tools_collab.py` / `tools_discuss.py` / `tools_evolution.py` / `resources.py`, each exposing `register(mcp)`, wired by `server._register_all()`. Adding a tool means updating the three tests that assert the exact tool set. `tools_collab.py` notifies ABOUT a record (Inbox bell); `tools_discuss.py` is user-to-user conversation (chat systray) — "message X" is the second, and its `channel_get` is the one method the verified `safety_layer.py` was extended to admit (L1).
- **`explore_module.REF_DIR` redirect** needs the package-qualified import (`from odoo_assistant.odoo_scripts import explore_module`) so the patch is visible to every importer. See `tools_evolution.py`'s docstring.
- **Wheel bundles `references_public/` only** (scrubbed, generic). `references/` holds the instance-specific set and is excluded.
- stdio transport: **nothing may reach stdout** but the JSON-RPC stream. Diagnostics go to stderr.
- Every write passes `server_safety.gate()` (L0–L5). L4/L5 refuse by default; `ODOO_MCP_MAX_LEVEL` moves the bar and refuses startup if invalid. Host consent dialogs are untrusted — **the gate is the only enforcement point**.

## Where guidance actually lands (measured)

Two controlled runs of a fresh host session showed the `odoo://skill` resource
being read **zero** times, and removing the equivalent global skill changed no
answer. What routed behaviour both times was a **tool docstring** — it becomes
the tool description, which every host puts in context unconditionally.

So a rule an agent must follow belongs in the tool's docstring, not in a
resource and not in a reference file. Structural guards in `safety_layer.py`
are the only thing stronger, because they refuse instead of advise.

## Odoo landmines (silent wrong results, not errors)

Full list: `references/SKILL.md` (8 rules) + `references/writing.md` (12 patterns).

- Odoo commits **before** serialising the response: an exception can mean "committed but unserializable" (`OdooExecutedButUnserializable`). **Never retry** — re-read. Blind retries once created duplicate invoices. Seen live: `crm.lead.action_set_lost` takes this path.
- A returned dict carrying `res_model` is a wizard to follow, not a result.
- `account.move` / `account.move.line` without `move_type` mixes invoices, bills and journal entries (3.613 vs 373). `check_guards()` blocks it structurally.
- Sum `*_signed` fields, never `amount_total` (multi-currency once inflated a total 11,9×).
- Multi-company: pass `context={"allowed_company_ids": [...]}` or you report one company as the whole business.
- **`default_get` is where records go missing.** `crm.lead.type` defaults to `'lead'` on an instance whose pipeline is opportunities; `account.move.move_type` defaults to `'entry'`, so a "new invoice" is a raw journal entry. The `required_fields` tool exists to surface this — call it before a create.
- **Archiving reads like deletion.** `action_set_lost` sets `active=False`; a later plain search returns `[]`. Add `["active", "in", [true, false]]` before concluding a record is gone.
- **`phone_sanitized` is one value per record**, computed from `mobile` first and `phone` second — not per field. It stays `False`, with no error, when the number has no `+` prefix and the record no `country_id`. Write E.164 yourself.
- Wrong `ODOO_USER` makes `authenticate()` return `False` rather than raise — it reads like a permission error. Omitting it entirely is supported and takes the discovery path (up to 59 probes, fails at uid >= 60).
- `create()` returns a list, not an int. Methods starting with `_` are always rejected.
- Idempotency: `Writer.create(..., unique_on=[...])`, chains via `Writer.step()`. A write is done only when a re-read proves it.

## Live and local testing

- Env: required are `ODOO_BASE_URL` (no trailing slash) and `ODOO_API_KEY` only. `ODOO_DB` is discovered (mandatory only when the instance serves several — the error names them), `ODOO_USER` is discovered from the key. **API key only** — passwords were removed deliberately.
- Write scenarios need `ODOO_MCP_ALLOW_LIVE_WRITE=1`; that variable is test-suite-only and must never appear in host config.
- Dev instance `persevida_dev18` (Odoo 18 Enterprise, companies ES+CZ, XML-RPC, destructive tests allowed). `odoo_client.py` refuses writes to hosts listed in `ODOO_MCP_PROTECTED_HOSTS` (env, empty by default — no host is hardcoded).
- Cleanup archives rather than deletes — `Writer.can()` refuses partner unlink — so `MCP Test %` residue on the dev instance is expected.
- Local host config lives in `.opencode/opencode.json`, **gitignored because it holds a real API key**. It runs the server from source (`uv run --directory <repo> odoo-assistant`) for development convenience, and raises `timeout` from opencode's 5000 ms default, which `instance_overview` exceeds.
- Smoke-test a script directly: `python3 src/odoo_assistant/odoo_scripts/query.py --url http://host:8069 --key <API_KEY>`

## references/

Generated by `explore_module.py`, never hand-written. Everything above
`## NOTES` is rebuilt on each run — hand-edit only the `## NOTES` section.
Served as MCP resources (`odoo://skill`, `odoo://ref/*`).
