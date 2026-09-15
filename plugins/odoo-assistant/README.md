# odoo-assistant plugin

Odoo Assistant as an installable plugin for Claude Code and Codex, straight
from this repository — no store, no review. The plugin wraps the same stdio
MCP server that `uvx odoo-assistant` runs.

## Before you install

Export the three environment variables the server needs, in the shell you
start your host from:

```bash
export ODOO_BASE_URL="https://mycompany.odoo.com"   # no trailing slash
export ODOO_API_KEY="your-api-key-here"             # Odoo 14+, Settings > Users > API Keys
export ODOO_DB=""                                   # required on Odoo Online (SaaS), discovered elsewhere
```

`uv` must be on your PATH (`curl -LsSf https://astral.sh/uv/install.sh | sh`),
because both manifests start the server with `uvx odoo-assistant`.

## Claude Code

```bash
/plugin marketplace add singleflo/odoo-assistant-mcp
/plugin install odoo-assistant@odoo-assistant
```

Then check `/mcp` shows the server. The marketplace and the plugin share the
name `odoo-assistant`, hence the `@odoo-assistant` suffix.

## Codex

```bash
codex plugin marketplace add https://github.com/singleflo/odoo-assistant-mcp
```

Then install from the Plugins Directory, "Personal" tab. The portable
manifests are `plugin.json` and `mcp.json` (Agent Plugins 1.0.0); the Claude
Code shapes are `.claude-plugin/plugin.json` and `.mcp.json`.

## Versions move together

`pyproject.toml`, `plugin.json`, `.claude-plugin/plugin.json` and the entry
in `.claude-plugin/marketplace.json` must carry the same version — release
tooling bumps them in one pass, and
`tests/test_marketplace_manifests.py` fails on drift.
