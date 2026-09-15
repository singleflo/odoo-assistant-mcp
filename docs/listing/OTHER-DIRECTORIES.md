# Secondary MCP Directories and Catalogs

> **Note:** None of the directory submissions listed below are executed as part of this plan. They serve as optional follow-up distribution steps for the repository owner after Milestone 2 (M2).

Publishing `odoo-assistant` to additional MCP marketplaces and catalogs increases discoverability beyond the main store listings. Our server is already live on the official MCP Registry:
`https://registry.modelcontextprotocol.io/v0.1/servers/io.github.singleflo%2Fodoo-assistant/versions`

Every entry below has been verified against its live page at execution time.

## Confirmed directories

### Cursor Marketplace

- **URL**: https://cursor.com/marketplace/publish
- **What to paste**: Identity (Name, Tagline, Long description), Logo asset (`docs/listing/icon.png`), and repository URL from `docs/listing/README.md`.
- **Transport**: stdio accepted (local `uvx odoo-assistant` configuration for Cursor `.cursor/mcp.json`).
- **Review required**: Yes (publisher review by Anysphere).
- **Cost**: Free.

### Cline MCP Marketplace

- **URL**: https://github.com/cline/mcp-marketplace
- **What to paste**: Open a GitHub Issue using the `mcp-server-submission.yml` template in `cline/mcp-marketplace`. Include repository link, 400x400 logo PNG (`docs/listing/icon.png`), and setup/usage details from `docs/listing/README.md`.
- **Transport**: stdio accepted.
- **Review required**: Yes (maintainer review via GitHub issue).
- **Cost**: Free (open source).

### Docker MCP Catalog

- **URL**: https://github.com/docker/mcp-registry
- **What to paste**: Submit a Pull Request adding server metadata to `servers/` in `docker/mcp-registry`. Reference the repository Dockerfile (`Dockerfile`), declared environment variables (`ODOO_BASE_URL`, `ODOO_API_KEY`, `ODOO_DB`), and description from `docs/listing/README.md`.
- **Transport**: stdio accepted (containerized stdio execution via Docker Desktop MCP Toolkit).
- **Review required**: Yes (Docker maintainer PR review).
- **Cost**: Free.

### Glama MCP Registry

- **URL**: https://glama.ai/mcp/servers
- **What to paste**: Automatically indexed from the official MCP Registry. Action required: log in to Glama and claim the listing using GitHub organization credentials.
- **Transport**: stdio and remote accepted.
- **Review required**: No (auto-indexed, claim verification via GitHub auth).
- **Cost**: Free.

### mcp.so Directory

- **URL**: https://mcp.so
- **What to paste**: Submit via the submission page (https://mcp.so/submit). Paste Name, Short/Long description, Category, Repository URL, and Documentation URL from `docs/listing/README.md`.
- **Transport**: stdio and remote accepted.
- **Review required**: Yes (community submission review via GitHub issue / portal).
- **Cost**: Free.

### VS Code MCP Gallery

- **URL**: https://code.visualstudio.com/docs/copilot/customization/mcp-servers
- **What to paste**: No action required. VS Code's extensions gallery and Copilot customization feed automatically from the official MCP Registry and VS Code extension marketplace.
- **Transport**: stdio and remote accepted.
- **Review required**: No separate submission (fed by official registry).
- **Cost**: Free.

### Gemini CLI Extensions

- **URL**: https://geminicli.com
- **What to paste**: Gemini CLI was deprecated and replaced by Antigravity CLI on June 18, 2026. A separate manifest (`gemini-extension.json`) is NOT worth a follow-up; standard `mcpServers` configuration in `~/.gemini/settings.json` via `uvx odoo-assistant` is sufficient.
- **Transport**: stdio accepted.
- **Review required**: No (client-side configuration).
- **Cost**: Free.

## Dropped directories

- **PulseMCP** (https://pulsemcp.com/submit): Gated behind Cloudflare Bot Management returning HTTP 403 Access Denied during automated verification. Dropped from active submission guidance.
