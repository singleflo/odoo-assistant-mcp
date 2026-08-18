# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
