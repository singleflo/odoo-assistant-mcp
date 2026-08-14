# Contributing to Odoo Assistant MCP Server

Thank you for contributing to the Odoo Assistant MCP Server. Please follow these guidelines to set up your development environment, run tests, and submit changes.

## Development Environment Setup

This project uses `uv` for Python package management.

1. **Install uv**:
   Follow the instructions at [astral.sh/setup-uv](https://astral.sh/setup-uv) to install `uv`.

2. **Clone the repository**:
   ```bash
   git clone https://github.com/crottolo/odoo-assistant-mcp.git
   cd odoo-assistant-mcp
   ```

3. **Sync dependencies**:
   ```bash
   uv sync
   ```

## Hard Rules for Code Changes

* **Treat `src/odoo_assistant/odoo_scripts/` as verified code, not as your working surface.** These 9 scripts are verified against a live Odoo instance and are the source of truth; the MCP server is a thin wrapper importing them. They came from an agent skill that has since been retired, so this repository is now the canonical copy — there is no upstream to sync from. Fix a bug here only with a live-instance test proving it, and never refactor them to suit the wrapper: adapt the wrapper instead.
* **Keep server.py thin.** Tools and resources must live in their respective modules (`tools_read.py`, `tools_write.py`, `tools_collab.py`, `tools_evolution.py`, `resources.py`) and expose a `register(mcp)` function.
* **No stdout printing.** The stdio transport uses stdout for JSON-RPC communication. Any diagnostic prints must go to stderr.

## Running Tests

We use `pytest` for testing.

### Non-Live Tests (Default)
To run the unit tests and mock-based integration tests:
```bash
uv run pytest tests/ -m "not live"
```

### Live Tests
To run tests against a live Odoo instance, you must opt-in by providing the connection details and setting the appropriate environment variables:

1. **Read-only live tests**:
   ```bash
   export ODOO_BASE_URL="https://your-odoo-instance.com"
   export ODOO_API_KEY="your-api-key"
   export ODOO_DB="your-db"
    export ODOO_USER="your-user" # Optional: omitted, the login is discovered from the key
   uv run pytest tests/
   ```

2. **Write live tests**:
   By default, live tests that perform write operations are skipped to prevent accidental modifications to your instance. To enable them, set `ODOO_MCP_ALLOW_LIVE_WRITE=1`:
   ```bash
   export ODOO_MCP_ALLOW_LIVE_WRITE=1
   uv run pytest tests/
   ```

## Commit Conventions

We follow standard git commit message conventions. Please write concise, descriptive commit messages matching the repository style. Stage only intended files and never commit secrets.
