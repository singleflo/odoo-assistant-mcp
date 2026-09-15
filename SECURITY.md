# Security Policy

## Data Access

The Odoo Assistant MCP Server acts as a bridge between your Odoo instance and the Model Context Protocol client. The server can access and modify any data that the configured Odoo user's credentials (API key) have permissions to access.

We recommend creating a dedicated Odoo user for the assistant with the minimum necessary permissions required for its tasks.

## Safety Layer and Blocked Operations

To prevent accidental data loss or unauthorized modifications, every write and action passes a gate that judges it by method name against two lists an operator owns, out of band, in the host configuration:

* **Denied by default** (`ODOO_MCP_DENY`): `unlink`, `archive`, `action_cancel`, `button_cancel`, `action_reverse`, `action_draft`, `mailing.mailing:action_send`. Setting the variable **replaces** this list rather than extending it, so the refusals in force are exactly the names in the operator's own file.
* **Allowed** (`ODOO_MCP_ALLOW`): `*` by default — every method not denied. The single value `none` makes the server read-only. Anything else is a comma-separated list of `method` (any model) or `model:method` (one model).
* **Deletion is locked separately**: `unlink` is refused unless `ODOO_MCP_ALLOW_UNLINK=yes`. Neither list can grant it, because deletion is the one action that cannot be undone and a name in a comma-separated list must never be enough to grant it.

Entries match by exact string equality — no prefix, no substring — so a lookalike method nobody reviewed cannot slip through an approved name. Deny is checked before allow. Reads are never subject to the lists, and methods starting with `_` are always refused, as is a query that Odoo itself would answer misleadingly (an `account.move` read with no `move_type` filter).

This is the authority of this server, not of the account. A limit that must hold regardless of the client belongs in the Odoo access rights of the user the API key belongs to, where the Odoo server enforces it.

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
