# Security Policy

## Data Access

The Odoo Assistant MCP Server acts as a bridge between your Odoo instance and the Model Context Protocol client. The server can access and modify any data that the configured Odoo user's credentials (API key) have permissions to access.

We recommend creating a dedicated Odoo user for the assistant with the minimum necessary permissions required for its tasks.

## Safety Layer and Blocked Operations

To prevent accidental data loss or unauthorized modifications, the server implements a dynamic safety classifier. Operations are classified into levels L0 to L5:

* **L0_READ**: Read-only queries. Always allowed.
* **L1_WRITE**: Single record writes and creations. Allowed by default.
* **L2_BATCH**: Batch writes affecting multiple records. Allowed by default.
* **L3_STATE_CHANGE**: Workflow state transitions (e.g., confirming orders, posting invoices). Allowed by default.
* **L4_DESTRUCTIVE**: Destructive operations (e.g., `unlink`, `action_cancel`, archiving). Blocked by default.
* **L5_PRIVATE / L5_UNKNOWN**: Private methods or unknown operations. Always blocked.

You can configure the maximum allowed level using the `ODOO_MCP_MAX_LEVEL` environment variable (default is `3`). To allow destructive operations, set `ODOO_MCP_MAX_LEVEL=4`. L5 operations cannot be allowed.

## Credential Flow

Credentials flow exclusively through environment variables:

* `ODOO_BASE_URL`
* `ODOO_DB`
* `ODOO_USER`
* `ODOO_API_KEY` (required — an API key, never an account password)

Account passwords are not accepted. An API key is per-user, scoped and
revocable without changing the account itself; a password is none of those.

The server never stores, logs, or transmits these credentials anywhere other than the direct XML-RPC or JSON-RPC connection to the configured Odoo instance. Credentials are never written to source code, packages, or configuration files.

## Reporting a Security Issue

If you discover a security vulnerability in this project, please report it by opening a GitHub issue or contacting the maintainer directly at the repository: https://github.com/singleflo/odoo-assistant-mcp.
