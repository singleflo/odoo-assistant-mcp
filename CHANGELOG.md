# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
