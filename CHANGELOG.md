# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.7] - 2026-09-24

### Fixed
- **Every tool now carries its title inside `annotations.title`.** The titles were there all along — but in the tool object's own `title` field, where the local hosts read them, and Anthropic's connector directory reads `annotations.title` instead and flags every tool without one: measured on the real submission portal, all 22 came back *"Missing title annotation"*. The hints were right, the grouping was right, and the one field that names a tool for a directory listing was sitting where that directory does not look. Both places now carry it: `Tool.title` keeps serving the hosts that read the tool field, `ToolAnnotations.title` the stores that read the annotations. The dossier test asserts a non-empty title per tool, so a tool registered without one fails the suite rather than a submission.

## [0.3.6] - 2026-09-19

### Fixed
- **A Discuss message that was delivered now says so.** Measured live on an Odoo 18 Enterprise instance: `message_post` answers with a value Odoo's own XML-RPC layer cannot serialise, so the client raised `OdooExecutedButUnserializable` — on **every** send, not only the first, and with the message already sitting in the channel. The caller therefore heard `COMMITTED but result unserializable … Verified state: NOT RE-READ`, which is accurate and useless: it reads as a malfunction, and the one thing it carries — do not retry — is exactly what a re-read settles for good. `send_direct_message` and `send_channel_message` now answer that exception the way this repo answers every write whose result is in doubt, by reading the record back: the message id returned comes out of the channel itself, which is stronger proof of delivery than the return value would ever have been. The post is never repeated. When the re-read cannot find the message — the newest in the channel is somebody else's — the exception passes through untouched and the cautious text is what the caller gets, because at that point nothing proves ours landed.

## [0.3.5] - 2026-09-19

### Added
- **The consent page now shows how to create an API key**, in seven steps with a screenshot of each. Until now it said the path in one sentence and promised illustrated steps "coming soon" — which is thin help on the one page where it matters: someone who does not have a key cannot connect at all, and this page is the only place they are certain to be standing. The walkthrough sits with the API key field rather than at the end of the page, stays collapsed so a reader who already has a key still sees a short form, and is titled as that reader's own question — *I don't have an API key — how do I make one?* — so the one who needs it recognises what to open. It covers what a key is (one user, that user's permissions, revocable on its own, never a password), the identity confirmation Odoo asks for, the duration field and what happens when it elapses, and the fact that the value is shown exactly once. The screens are Odoo 18 and the note names the Odoo 19 difference: description and expiry both required, three months at most.
- **`docs/api-key.md`** is the same walkthrough for the reader who never reaches the consent page: someone configuring a local install edits a host config file and never sees the hosted server. The README's *API Key Generation Path* keeps its one-line answer and now links there. The page reuses the screenshots already in the package rather than copying them, and `docs/` is excluded from the sdist, so this costs the distribution nothing.
- **`/img/<name>`** serves those screenshots from this origin, so the Content Security Policy stays `default-src 'none'` with no third party invited onto a page where an API key is typed. The route answers only the seven names it knows — the request never reaches a filesystem read unless it matched the list first — and the version rides on the query, like the stylesheet, so a deploy invalidates the year-long immutable cache.

## [0.3.4] - 2026-09-17

### Added
- **`read_long_field`** — one long text field, read in windows. The 5000-character result cap protects the model's context, but it also made a single oversized value **unreachable past its first cut**: narrowing the field list or the domain cannot help when that one value already is the whole answer, and paging with `offset` returns the same cut text forever. Measured on a live lead: `read_record` came back cut and no combination of arguments could reach the rest of its description. This tool reads that field alone and returns a 4000-character window with the total length and the offset of the next window, so the value can be walked to the end — the window is sized so an answer never hits the cap that caused the problem. An empty field says it is empty rather than returning a window of nothing.

### Changed
- **The truncation notice names the remedy for each cause.** It now reads: narrow fields/domain, `group_records` for counts, `read_long_field` for text. The cap is announced in the notice and in the `read_record` and `search_read` docstrings, so an agent that meets it is told what to call next instead of retrying a call that cannot succeed.

## [0.3.3] - 2026-09-17

### Added
- **`group_records`** — grouping with counts (Odoo `read_group`). Measured on a real session: an agent building "records per state, per stage, per month" issued 138 `count_records` calls, one per bucket, half of them answering zero. This tool returns every existing bucket with its count — or an aggregate like `amount_total_signed:sum` — in one `read_group` call, always in `lazy=False` mode so the rows are flat and no follow-up call per group is needed. Odoo's per-row bookkeeping (`__domain`, `__range`, `__fold`) is stripped before the answer travels, and the count sits under a stable `count` key instead of a wire spelling that changes with the mode. `amount_total` is refused as an aggregate on `account.move` / `account.move.line` — per-record currency, the 11,9× lesson — pointing at the `_signed` twin.
- **`describe_model`** — a model's field dictionary in one call (Odoo `fields_get`): name, type, relation, selection values, required starred. The alternative measured in the same session was 15 `search_read` calls against `ir.model.fields`. For what a `create` demands, `required_fields` still reads deeper.
- **Parameter descriptions on the wire.** Both new tools declare their arguments with pydantic `Field` descriptions, so the JSON Schema properties a host reads carry the format and the caveats — the `Args` prose previously lived only in the tool description. pydantic is now declared as a direct dependency (it always arrived through the SDK).

### Changed
- **The truncation notice teaches the right remedy.** "use limit/offset" was the advice, and a measured session followed it — five identical `search_read` calls paging through a cut result. The notice now says: fewer fields, tighter domain, or `group_records` for counts. `count_records` and `search_read` docstrings route breakdowns to `group_records` in the same spirit.

## [0.3.2] - 2026-09-16

### Fixed
- **Signing in when the API key's owner has a high uid.** XML-RPC takes the uid as a parameter of the call, and Odoo derives the login from that uid rather than from the key (`res.users.check` builds the credential out of `env.user.login`), so a client holding only a key cannot ask who owns it and falls back to probing `res.users` for uid 1 to 59. A user created after the first few dozen sits past that — measured at 687 and 691 on a live instance — and every sign-in failed with a message telling the person to set `ODOO_USER`, an environment variable nobody signing in through a browser has anywhere to put. The consent page now offers an optional **Odoo login**, which resolves the uid in a single `authenticate` call at any value, and stores it with the tenant so later calls never probe. Left empty, discovery runs exactly as before.
- **The login that gets stored is the one Odoo reports**, not the one typed. A wrong login is not an error while the probe can still rescue it, so what was submitted may be a typo the sign-in survived — keeping it would persist a value Odoo refuses and pay for a failed `authenticate` on every later connection. Reading it back also retires the probe for tenants that never filled the field in: they pay for it once, on the sign-in, and never again.
- **Verification failures read as sentences.** The text came from a subprocess as an exception repr, so the page showed `MissingCredentials("...\nSet ODOO_USER to...")`, class name and escapes included. The two failures a person can act on now say what to do, and anything else is unwrapped to the message the client wrote.

## [0.3.1] - 2026-09-16

### Fixed
- **The consent form's pending state.** The spinner was attached to the submit event's `submitter`, so a submission that supplied none left no button marked at all, and the pressed button was dimmed along with every other; now the script falls back to the primary button, swaps its label to "Connecting…", marks the form `aria-busy`, dims only the buttons that are not busy, and hides the ring under reduced motion where the label carries the cue instead.

### Changed
- **The palette.** Neutral structure with Odoo's own hues carried only by actions, links and selected states (plum `#714B67`, teal `#017E84` as the focus ring), Odoo's neutrals for surfaces and text, and a dark scheme that is this project's derivation because Odoo ships none. The font stack now names Inter first and falls back to the system stack — no web font is loaded, the pages still make no external request.
- **The landing page states its independence from Odoo S.A.** under the headline, not only in the footer.

## [0.3.0] - 2026-09-15

The hosted server. The same nineteen tools are now also served over the
internet at `https://mcp.singleflo.com/mcp`, which Claude.ai, ChatGPT and Codex
reach with nothing installed and nothing configured on the user's side: the
sign-in happens once, on the server's consent page, with the user's own Odoo
URL and API key. The local stdio server is untouched by all of it — a 0.2.0
configuration file is a 0.3.0 configuration file.

### Added
- **`odoo-assistant-remote`, the hosted server** (`odoo-assistant[remote]`, entry point `odoo-assistant-remote`): Streamable HTTP with its own OAuth 2.1 sign-in — the host starts the flow and lands on the consent page, which asks for the Odoo URL, the API key and one choice: **read** lets the agent look, **standard** lets it also create records, run workflows and schedule activities. Deletion is never available hosted — `unlink` is not reachable under either choice, so a chat can ask all it wants. Credentials are per tenant, encrypted at rest, and the choice made at consent is what gates every later call; the local `ODOO_MCP_*` lists do not follow the user onto the hosted route.
- **Downloaded documents and rendered PDFs as fifteen-minute links.** On the hosted server `download_docs` and `generate_pdf` return links that expire a quarter of an hour after they are issued, so a chat transcript never becomes a permanent copy of an Odoo document — the bytes stay on the server and outlive neither the conversation's need for them.
- **A title and annotations on all nineteen tools.** Every tool now declares `title`, `readOnlyHint`, `destructiveHint`, `idempotentHint` and `openWorldHint`, so a host can label and pre-approve calls from the tool's own declaration instead of guessing — ChatGPT honours `readOnlyHint` in its developer mode, which makes the reads self-evident there.
- **Claude Code and Codex marketplace manifests** under `plugins/odoo-assistant/` — the portable Agent Plugins shape (`plugin.json`, `mcp.json`) and the Claude Code shape (`.claude-plugin/plugin.json`, `.mcp.json`), installable straight from this repository without a store or a review.
- **Privacy, terms and support pages served on the same origin** as the server itself, so the consent page and the store listings point at URLs under the operator's own domain rather than a foreign wiki.
- **A `Dockerfile` and a Coolify-ready `docker-compose.yaml`**, and `docs/REMOTE.md` — the whole path from `uv run odoo-assistant-remote` on a laptop, through a tunnel, to a deployed instance behind its own domain.

### Changed
- **Nothing, for stdio users.** Same environment contract, same nineteen tools, same gate. `download_docs` and `generate_pdf` behave differently only on the hosted server, where they return expiring links instead of local paths — there is no local disk to write to there.

### Security
- **API keys are Fernet-encrypted at rest** in the hosted store, and tokens are stored hashed. Revocation is per family: a disconnect in Claude or ChatGPT erases the stored connection — the encrypted credentials included — not just the one token the revocation arrived with.
- **The remote refuses to start with any `ODOO_*` credential in its environment.** A hosted process carrying the operator's own `ODOO_BASE_URL` or `ODOO_API_KEY` would lend that login to every tenant; it exits instead, because the store supplies per-tenant credentials and must never inherit the deployer's.
- **An SSRF guard on the consent endpoint.** The Odoo address typed into the consent page is resolved and refused when it points at a loopback, link-local, private or unspecified address — before any connection is attempted — so a sign-in cannot be aimed at the server's own network.

## [0.2.0] - 2026-09-15

The numbered safety ceiling is gone, replaced by two lists of Odoo method names. The scale answered "what does this do"; the question an operator actually has is "may it run", and people answer that in method names — nobody could explain in a minute which number allowed `action_confirm` but refused `unlink`.

**BREAKING**: a method nobody reviewed is now allowed by default. `ODOO_MCP_ALLOW=*` is a real wildcard, where 0.1.2 refused every unclassified method (`L5_UNKNOWN`). The default deny list was extended with `mailing.mailing:action_send` specifically because a mass mailing is the one measured case where that matters: 31 models answered to `action_send` on a live instance, and only the Evolution wizards should. The entry is model-qualified for that reason — `action_send` elsewhere keeps working. There is no compatibility shim: a server still started with `ODOO_MCP_MAX_LEVEL` refuses to start.

### Changed
- **`ODOO_MCP_ALLOW` and `ODOO_MCP_DENY` replace the L0–L5 ceiling.** The gate now decides by method name, matched as exact string equality on `method` or `model:method` — no prefix, no substring, so `action_cancel` never matches `button_cancel`. Deny is checked before allow. `ODOO_MCP_ALLOW` defaults to `*`; `none` makes the server read-only; a value set on `ODOO_MCP_DENY` REPLACES the default list rather than extending it, which is how an operator re-enables `action_cancel`. `unlink` is decided before both lists and is granted only by `ODOO_MCP_ALLOW_UNLINK=yes`, because deletion is the one action that cannot be undone and a name in a comma-separated list must never be enough to grant it. All three are read at call time, so a host-config edit is the whole story. Reads are never subject to the lists; the structural guards (`account.move` without `move_type`, private `_` methods) still refuse regardless of what either list says.
- **Startup refuses while `ODOO_MCP_MAX_LEVEL` is still set.** Any value counts, including a stale `0` that used to mean read-only — silently ignoring it would turn a server the operator configured read-only into a writing one. The error names the three replacements.
- **The server warms its connection up in a background thread at startup** instead of connecting on the first tool call, so the first call no longer pays for authentication. A failed warm-up degrades to a stderr warning and the tools retry.

### Removed
- **`ODOO_MCP_MAX_LEVEL`**, with the classification ceiling it configured.
- **`ODOO_MCP_PROTECTED_HOSTS`**, **`ODOO_ALLOW_PROD_WRITE`** and **`ODOO_PROFILE_DIR`** — no longer read by the server. The server no longer arms the protected-host guard: it was a second answer to a question the gate already answers, and read-only intent is now expressed as `ODOO_MCP_ALLOW=none`, in the same file and the same vocabulary as every other permission. The first two remain read by the CLI scripts in `odoo_client.py`, which are used outside the MCP path.

### Added
- **`docs/INSTALL-WITH-YOUR-AGENT.md`**: the install path for people who let their agent do the configuring.
- **An expanded host configuration section** in the README, carrying the allow/deny variables in the file the human owns.
- **A "Database and login" guidance section**, stating when each is discovered and when it must be set.

## [0.1.2] - 2026-08-25

Everything here was found by pointing the server at a live Odoo **16.0** Enterprise instance for the first time. 16.0 is now verified for connection, authentication, reads, `instance_overview` and Discuss; write scenarios were not exercised.

### Fixed
- **Every error message this server produces had become invisible.** MCP SDK 2.1.0, released after 0.1.1 shipped, stopped forwarding the text of any exception that is not its own `ToolError` — a deliberate decision not to leak arbitrary exception text to clients. `ToolExecutionError` inherited from `RuntimeError`, which put all of it on the wrong side: instead of "Missing Odoo credentials: ODOO_BASE_URL, ODOO_API_KEY", or a refusal naming the safety level required, or a structural guard explaining itself, a caller saw `Error executing tool search_read` and nothing more. It now inherits the SDK's `ToolError`, which carries the message on 2.1.0 and on 2.0.0 alike, and is still not an `MCPError`, so no JSON-RPC error code is ever produced. **0.1.1 is affected** for anyone whose environment resolved 2.1.0. Caught by the wheel end-to-end test, which installs the built artifact unlocked and therefore saw the new SDK while `uv.lock` held development at 2.0.0 and the unit suite stayed green.
- **The four Discuss tools failed on Odoo 16.** Odoo 17 renamed `mail.channel` / `mail.channel.member` to `discuss.channel` / `discuss.channel.member`, and the new names were hardcoded at eleven call sites, so every Discuss tool answered "Object discuss.channel doesn't exist" on 16. The pair is now resolved by asking `ir.model` — the version string is a marketing label, the model table is the fact — and cached per client, so it costs one probe per connection and a reconnection cannot inherit the previous instance's answer. Verified live that only the names moved: every field these tools read, and `channel_get`, exist unchanged under the old model.
- **The census wrote ERROR tracebacks into other people's production logs.** It learned what a database contains by querying things that may not exist and waiting to be refused, while `_safe` swallowed the failure on our side — so the operator saw errors we never reported. `sale.order.subscription_state` does not exist before Odoo 17 (subscriptions were their own model), and that one call produced `ValueError: Invalid field sale.order.subscription_state` on a customer's server. The field is now confirmed with `fields_get` first, and `has_model()` consults `ir.model` instead of provoking a refusal. Measured on that instance: five tracebacks per refresh became zero, with 18.0 still reporting every area unchanged.
- **`instance_overview` cut its summary mid-field.** A bare `[:112]` truncation could end a line on a lone label, so an instance holding 22.385.318,32 in invoiced total displayed `invoiced_total_company_currency` with no value and silently dropped three further figures. Only whole `key=value` fields are elided now, and the elision is marked.

### Added
- **`Changelog` in `[project.urls]`**, which PyPI renders in the sidebar. Until now the package page offered no route to the release history at all.

### Documentation
- **`ODOO_DB` is mandatory on Odoo Online**, and the README said it was optional. SaaS disables the database-list endpoint, so discovery finds nothing and every call fails with an opaque host error that never mentions the database. Each variable now states *when* it is mandatory, the version table separates what is verified live from what is merely protocol-compatible, and the host examples carry a read-only Odoo Online configuration.

## [0.1.1] - 2026-08-18

### Fixed
- **MCP Registry project URL**: the `MCPRegistry` entry in `[project.urls]` pointed at `https://registry.modelcontextprotocol.io/servers/io.github.singleflo/odoo-assistant`, which returns 404 and always did — the registry exposes no `/servers/{name}` detail endpoint, the `/v0.1` API prefix was missing, and the `/` inside the server name has to be percent-encoded as `%2F`. It now points at the verified `https://registry.modelcontextprotocol.io/v0.1/servers/io.github.singleflo%2Fodoo-assistant/versions`, which returns 200 and the server's own metadata. `[project.urls]` is baked into published PyPI metadata and is immutable for a released version, so 0.1.0's broken link could only be corrected by publishing a new version.

## [0.1.0] - 2026-08-18

This is the first published release of the Odoo Assistant MCP Server.

### Added
- **MCP Server Core**: Implemented `MCPServer` skeleton with environment credentials validation and stderr logging.
- **Safety Layer**: Dynamic classification gate (L0 to L5) with configurable `ODOO_MCP_MAX_LEVEL` ceiling.
- **Error Handling**: Custom error models, `OdooExecutedButUnserializable` handling, and output truncation strategy.
- **Read Tools**: Implemented `search_read`, `read_record`, `count_records`, and `instance_overview` with company context and caps.
- **Write Tools**: Implemented `create_record`, `write_record`, `run_action`, and `cancel_record` with idempotency and verification.
- **Collaboration & Document Tools**: Implemented `notify_user`, `create_activity`, `download_docs`, and `generate_pdf` with audience guards.
- **Discuss Tools**: Implemented `list_message_targets`, `read_conversation`, `send_direct_message`, and `send_channel_message` to support user-to-user messaging.
- **Evolution Tools**: Implemented `explore_module` with persistence redirect and `list_known_modules`.
- **Resources**: Exposed `odoo://skill` and `odoo://ref/*` from the bundled reference set.
- **Testing**: Added mock-based unit tests, scripted MCP Inspector suite, and opt-in live integration suite with cancel-based cleanup.
- **CI/CD**: Configured tag-triggered sequential PyPI and MCP Registry publishing pipeline using GitHub OIDC.
- **Documentation**: Added PyPI README, host configuration examples, security policy, and developer guidelines.
- **Credentials**: Only `ODOO_BASE_URL` and `ODOO_API_KEY` are required; the database and the login are discovered from the key.
- **Paths**: A single per-OS data directory for caching and persistence, with `ODOO_MCP_DATA_DIR` to override it.
- **Notifications**: `notify_user` offers three subtypes — `note` (internal, visible in the chatter), `inbox` (notification only, invisible on the record) and `comment` (emails every follower).
