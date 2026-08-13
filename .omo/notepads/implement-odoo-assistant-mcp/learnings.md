# Learnings — implement-odoo-assistant-mcp

Conventions, patterns, and successful approaches discovered during work on this plan.

_Auto-scaffolded by /start-work. Append new entries below - never overwrite._

---

## [2026-08-13T13:15Z] Task: start-work bootstrap (Atlas orchestrator)
- **GitHub identity verified live**: `gh auth status` → account `crottolo` (NOT `singleflo` as the PRD/draft assumed). Plan corrected: repo = `crottolo/odoo-assistant-mcp`, MCP registry namespace = `io.github.crottolo/odoo-assistant`, mcp-name comment updated accordingly in todos 19-21,24. Any future todo referencing `singleflo` is STALE — use `crottolo`.
- **Live dev instance verified reachable** (read-only `query.py` smoke test, no writes): `http://dev8069:8069`, Odoo 18.0 Enterprise, 285 modules, transport=xmlrpc (JSON-2 absent, confirms AGENTS.md). Companies: **[2] Persevida S.L. (Spain)**, **[1] FL1 s.r.o. (Czech Republic)** — use `context={"allowed_company_ids":[1,2]}` for multi-company reads in T11/T16. Sanity figures (for T16 tolerant-range assertions): out_invoice=377, in_invoice=653, out_refund=17, sale.order confirmed=133, partners customers=181, helpdesk open=6, 31 in-house modules.
- **Credentials handling**: ODOO_BASE_URL + ODOO_API_KEY for this dev instance are held by the orchestrator (Atlas) in-session and passed directly into delegation prompts ONLY for todos that need live access (T16, and any live smoke check) — NEVER written to a repo-tracked file, NEVER passed to Wave-1/Wave-2/Wave-3 subagents that don't need them. `.gitignore` (T1) additionally excludes `.env*` and `*.local` as defense in depth.
- **User explicit request**: initialize the GitHub repo now (not deferred to T24) and commit periodically. T1 extended accordingly (git init → gitignore → pre-commit → first commit → `gh repo create --push`). T24 still owns the PyPI/MCP-Registry OIDC prerequisites only (those genuinely need human 2FA/web UI).

## [2026-08-13T13:30Z] Task: pyproject and package shims (todo 2)
- `uv sync` required a present `README.md` for Hatchling metadata validation and a present `references_public/` directory for the requested force-include mapping; the latter is currently an empty placeholder anticipating the parallel references todo.
- Resolved and installed `mcp==2.0.0` from the exact `mcp[cli]>=2.0.0,<3` constraint. The package does not expose `mcp.__version__`; use `importlib.metadata.version("mcp")` for verification.
- `pytest` was added to the `dev` dependency group so the requested `uv run pytest tests/ -m "not live"` command works in the synced environment; it collected zero tests without configuration errors.

## [2026-08-13] Task 4: project-level agent skills
- Commands completed successfully without -g:
  - npx skills add anthropics/skills --skill mcp-builder -y
  - npx skills add mattpocock/skills --skill setup-pre-commit -y
  - npx skills add mattpocock/skills --skill tdd -y
- Installed universal skill files:
  - .agents/skills/mcp-builder/SKILL.md
  - .agents/skills/setup-pre-commit/SKILL.md
  - .agents/skills/tdd/SKILL.md
  - .claude/skills/mcp-builder/SKILL.md (CLI-created symlink)
  - .claude/skills/setup-pre-commit/SKILL.md (CLI-created symlink)
  - .claude/skills/tdd/SKILL.md (CLI-created symlink)
- Verification: find . -iname "SKILL.md" -not -path "./.venv/*" found all three named skills.
- Verification: find . -iname "*fastmcp*" -not -path "./.venv/*" returned no matches.
