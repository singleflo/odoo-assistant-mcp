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

## fix-c-deploy-e2e (2026-09-15)

- deploy.yml now gates the Coolify webhook on a `tests` job (F1#11, F4#3):
  same matrix-less essentials as tests.yml (`uv sync --extra remote`,
  pytest `-m 'not live and not wheel and not remote_live'`, `uv build`);
  `deploy` runs only `needs: tests` green, secrets guard unchanged.
- RELEASE-CHECKLIST Coolify step 9 gained the one measured note F1 wanted:
  bind mounts (if anyone adds one) resolve against the service configuration
  directory on the Coolify server — the reason the compose uses the named
  volume `mcp_data` for `/data`. Other prose untouched.
- test_remote_live.py (F1#16, F4#2): gate is now FOUR env vars
  (ODOO_REMOTE_TEST_DB joins; policy stays optional, default read). Count is
  a pair: id-0 zero-canary AND `domain []` asserted > 0 (pure read). New
  two-tenant test mints a standard-policy token via scripts/remote_token.py
  and proves the SAME write_record id 0 call is gate-refused read-only under
  tenant A but passes the gate under tenant B, failing as an Odoo error on
  the nonexistent id — safe by construction, nothing written either way.
  New PDF test: under tenant B (standard — rendering goes through the
  print/send wizard, a write-class call), res.partner id 0 must error naming
  res.partner with NO /files/ link; with the optional
  ODOO_REMOTE_TEST_PDF_MODEL/ODOO_REMOTE_TEST_PDF_ID pair it must return a
  /files/ URL whose HEAD answers 200, application/pdf,
  Cache-Control private+no-store.
- ASSUMPTION recorded: the task asked the PDF error text to "mention the
  Odoo failure"; under a read-policy tenant the gate refuses
  action_send_and_print BEFORE Odoo, so the error path deliberately runs
  under the standard token, where the text really is the Odoo-side failure
  ("No known print wizard for res.partner").
- scripts/remote_live_check.sh takes the URL as $1 (default
  $ODOO_REMOTE_PUBLIC_URL, then https://mcp.singleflo.com) and prints
  PASS/FAIL per check; exit nonzero when any fails. Proven green against a
  locally spawned odoo-assistant-remote (3 PASS, exit 0) and marking on a
  dead port (3 FAIL, exit 1).
- Suite: 346 passed / 31 deselected; `pytest -m remote_live
  tests/test_remote_live.py` = 11 skipped without env (CI never runs it).
   test_remote_live.py sits at 234 pure LOC — warning band; next edit there
   should split the plain-HTTP probes from the tokened flow.
- Promotion follow-up: same (url, API key) re-consent upgrades the shared tenant; earlier tokens follow the current policy, while API_KEY_2 remains isolated.

## Fix round D2 (final-wave F2 re-review, all four findings)

- consent.py (F2#1, DoS): `_verify_credentials` no longer runs on a thread
  anyio cannot cancel (the scripts have no socket timeout, and they are
  canonical, so they cannot gain one). `_verify_isolated` runs it in a
  `multiprocessing.get_context("spawn").Process` (daemon), passing only
  odoo_url/db/api_key plus a `Pipe`; the parent joins `_VERIFY_TIMEOUT`,
  then `terminate()` -> `kill()` with a 2 s grace (`_reap`). The HTTP
  request always answers within the timeout; no worker thread hangs and no
  process survives (`consent._last_verifier` keeps the last child's handle;
  the rewritten test polls it). Child target `_verify_entry` writes
  ("ok","") or ("error", repr(exc)); EOF (child died silent) renders the
  generic refusal. Flow tests pin the PARENT seam `_verify_isolated` now —
  a spawn child re-imports the module fresh, so patching `connect` (old
  fixtures in test_remote_consent/test_remote_app) stopped intercepting;
  both fixtures were rewired and still record the identical
  {"base","db","user","key"} shape, so no assertion text changed.
- files.py (F2#2): new `purge_tenant_artifacts(subject)` rmtree's
  `data_dir()/files/<subject>` AND `data_dir()/references/<subject>` under
  the OVERRIDDEN root, with publish's exact "/"+".." guard (now shared as
  `_checked_subject`). Called from auth.py `revoke_token` right after
  `delete_tenant` and from the sweep for every idle-removed tenant.
- app.py (F2#3): lifespan now sweeps periodically — `SWEEP_SECONDS = 3600`
  (module constant next to the lifespan so REMOTE.md-style docs cite it),
  `_sweep_once(store)` (purge_expired + purge_idle_tenants +
  purge_tenant_artifacts per removed subject + purge_expired_files) runs at
  startup AND in `_sweep_forever` via an anyio task group inside the
  wrapped SDK lifespan; a failing sweep logs and retries next tick instead
  of killing the server. GOTCHA worth remembering: anyio task groups WAIT
  for children on normal exit, they do not cancel them — without an
  explicit `sweeps.cancel_scope.cancel()` in `finally` the shutdown hangs
  forever (TestClient and uvicorn alike). store.py `purge_idle_tenants`
  now RETURNS the removed subjects (signature only; no schema change;
  test_remote_store callers unaffected).
- paths.py (F2#4): `set_data_dir_override(path|None)` + `data_dir()`
  consulting it first; `build_app` pins `settings.data_dir` once at
  startup. `files.configure_data_dir` stays as a thin alias;
  `files._base_dir` captured global is GONE — every files.py path derives
  from `paths.data_dir()` at call time, so tools_collab/_tenant_dir and
  tools_evolution became correct without touching them. Test proves
  build_app + publish + `_tenant_dir` all land under one custom root.
- Test isolation: the process-wide override needed autouse resets —
  test_remote_app sets `paths._data_dir_override = None` via monkeypatch
  per test, test_remote_files does it in its data_dir fixture; without
  this the pin from an earlier module leaks into env-based tests later in
  the alphabetical run.
- Suite: 353 passed / 32 deselected, stable across two consecutive runs
  (evidence .omo/evidence/fix-d-retention.txt has the red run: 8 failed +
  14 fixture errors, all naming the missing seams).
- DEBT flag: consent.py is now 295 pure LOC (over the 250 ceiling). The
  natural split when someone picks it up: the verification machinery
  (`_verify_entry`/`_verify_isolated`/`_reap`/`_Verified`) into
  remote/verify.py; not done here because the round's scope fixed the file
  list and remote/ siblings were frozen.
- FIXED (F2 regression, bisected to 3a42ddf): the `_own_data_root` fixture cleared
  `paths._data_dir_override` but not the global DERIVED from it, so
  `explore_module.REF_DIR` stayed pinned to a dead tmp_path and failed
  test_evolution in the same run; teardown now re-runs
  `tools_evolution._redirect_references()`, pinned by
  test_reference_dir_does_not_survive_a_custom_data_root. Two privacy.md
  retention lines aligned with the code (hourly sweep, link-expiry wording).
