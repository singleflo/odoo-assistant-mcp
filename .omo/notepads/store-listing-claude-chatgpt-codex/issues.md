# Issues — store-listing-claude-chatgpt-codex

Problems and gotchas encountered during work on this plan.

_Auto-scaffolded by /start-work. Append new entries below - never overwrite._

---

## 2026-09-15 — Final Wave F2 security fixes

- Confirmed the consume races before the fix: 5/20 pending winners and 4/20
  authorization-code winners in one synchronized run. Both native SQLite
  `DELETE RETURNING` and the forced pre-3.35 `BEGIN IMMEDIATE` fallback now
  produce exactly one winner.
- DCR client JSON required the same Fernet-at-rest treatment as tenant keys;
  authorization-code values now use SHA-256 lookup keys and exist raw only in
  process memory.
- OAuth pair creation/rotation and family revocation now use immediate SQLite
  transactions; injected pair-write failures prove rollback.
- Tenant clients are keyed by `(subject, db, base_url)` and all cached entries
  for a deleted subject are evicted. Idle tenant purge also evicts its clients.
- `remote.files` had captured the platform default and polluted real user data.
  `build_app` now configures it from `settings.data_dir`; the test-created
  `t_seed`, `t_configured`, and `remote.db*` artifacts were listed and removed.
- SSRF policy is now an `is_global` allowlist. CGNAT `100.64.0.0/10` is refused.
- AnyIO cancellation now abandons the verification wait. The worker can linger;
  canonical `odoo_scripts/` were not changed to add socket timeouts.
- Pre-fix development databases have no migration path by design and should be
  deleted because this hosted database has not shipped.

## fix-b-docs-truth (2026-09-15)

- The Agent Plugins 1.0.0 plugin schema has NO `support` field (checked
  https://agent-plugins.org/schemas/1.0.0/plugin.schema.json: properties are
  $schema, name, version, description, author{name,email,url}, homepage,
  repository, license, keywords, extensions). Fallback applied: websiteURL
  stays on the repo root and the issues URL is noted in
  plugins/odoo-assistant/README.md. `extensions` is free-form
  (additionalProperties: object), so no schema change is possible without
  inventing a field.
- `tools_evolution.register()` used ONE `reads` ToolAnnotations constant for
  BOTH explore_module and list_known_modules. Flipping explore_module forced
  a split (writes_reference vs reads); list_known_modules keeps read-only.
- The old combined dossier-coverage check hid that SUBMIT-CLAUDE.md never
  mentioned Country availability — SUBMIT-OPENAI.md was covering for it.
  The per-guide check now prevents that class of drift.
- Old privacy.md claimed an "encrypted database"; the DB is plain SQLite
  with field-level Fernet encryption. Any store reviewer diffing copy vs
  code would have caught the overstatement.
