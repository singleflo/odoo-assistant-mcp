# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-08-13

### Added
- **MCP Server Core**: Implemented `MCPServer` skeleton with environment credentials validation and stderr logging.
- **Safety Layer**: Dynamic classification gate (L0-L5) with configurable `ODOO_MCP_MAX_LEVEL` ceiling.
- **Error Handling**: Custom error models, `OdooExecutedButUnserializable` handling, and output truncation strategy.
- **Read Tools**: Implemented `search_read`, `read_record`, `count_records`, and `instance_overview` with company context and caps.
- **Write Tools**: Implemented `create_record`, `write_record`, `run_action`, and `cancel_record` with idempotency and verification.
- **Collaboration & Document Tools**: Implemented `notify_user`, `create_activity`, `download_docs`, and `generate_pdf` with audience guards.
- **Evolution Tools**: Implemented `explore_module` with persistence redirect and `list_known_modules`.
- **Resources**: Exposed `odoo://skill` and `odoo://ref/*` from the bundled reference set.
- **Testing**: Added mock-based unit tests, scripted MCP Inspector suite, and opt-in live integration suite with cancel-based cleanup.
- **CI/CD**: Configured tag-triggered sequential PyPI and MCP Registry publishing pipeline using GitHub OIDC.
- **Documentation**: Added PyPI README, host configuration examples, security policy, and developer guidelines.
