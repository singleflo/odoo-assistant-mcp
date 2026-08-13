<!-- mcp-name: io.github.crottolo/odoo-assistant -->
# Odoo Assistant MCP Server

An Odoo virtual employee via the Model Context Protocol (MCP). This server exposes Odoo's business logic, records, and workflows to LLMs, allowing them to query, create, update, and act on Odoo data safely.

## Quickstart

### 1. Install
Install the package from PyPI using `uv` or `pip`:
```bash
uv pip install odoo-assistant
```

Or run it directly using `uvx`:
```bash
uvx odoo-assistant
```

### 2. Configure Environment Variables
The server requires the following environment variables to connect to your Odoo instance:

* `ODOO_BASE_URL`: The base URL of your Odoo instance (e.g., `https://mycompany.odoo.com`).
* `ODOO_DB`: The database name.
* `ODOO_USER`: The username or email of the Odoo user.
* `ODOO_API_KEY`: The Odoo API key — **required** (Odoo 14+, generate under Settings > Users > API Keys > New). An account password is not accepted: a key is per-user, scoped and revocable on its own.

Optional configuration:
* `ODOO_MCP_MAX_LEVEL`: The maximum safety level allowed (default: `3`).

## Safety Layer

Every write and action passes through a dynamic safety classifier before reaching Odoo. Operations are classified into levels L0 to L5:

| Level | Name | Description | Default Status |
|---|---|---|---|
| **L0** | `L0_READ` | Read-only queries (`search_read`, `read`, `search_count`). | Allowed |
| **L1** | `L1_WRITE` | Single record writes and creations. | Allowed |
| **L2** | `L2_BATCH` | Batch writes affecting multiple records. | Allowed |
| **L3** | `L3_STATE_CHANGE` | Workflow state transitions (e.g., confirming orders, posting invoices). | Allowed |
| **L4** | `L4_DESTRUCTIVE` | Destructive operations (e.g., `unlink`, `action_cancel`, archiving). | Blocked |
| **L5** | `L5_PRIVATE` / `L5_UNKNOWN` | Private methods or unknown operations. | Blocked |

The `ODOO_MCP_MAX_LEVEL` environment variable sets the ceiling (default is `3`). Operations above this ceiling are blocked. Destructive operations (L4) and private/unknown operations (L5) are blocked by default. To allow destructive operations, set `ODOO_MCP_MAX_LEVEL=4`. L5 operations are always blocked.

## Odoo Version Support

Odoo 14.0 is the absolute minimum supported version because this server authenticates using API keys only, which do not exist in Odoo 13 or earlier.

| Odoo Version | API Keys | XML-RPC | Officially Maintained (Aug 2026) | Support Level / Notes |
|---|---|---|---|---|
| **≤ 13.0** | **No** | Yes | No | **Unsupported**. Cannot authenticate with this server. |
| **14.0** | **Yes** | Yes | No | Protocol-compatible. Untested against a live instance. |
| **15.0** | Yes | Yes | No | Protocol-compatible. Untested against a live instance. |
| **16.0** | Yes | Yes | No | Protocol-compatible. Untested against a live instance. |
| **17.0** | Yes | Yes | **Yes** (until Sep 2026) | Protocol-compatible. Untested against a live instance. |
| **18.0** | Yes | Yes | **Yes** (until Sep 2027) | **Primary target**. Verified and fully supported against a live instance. |
| **19.0** | Yes | Yes | **Yes** (until Sep 2028) | Protocol-compatible. Untested against a live instance. API keys require description and expiry (max 3 months). |

### API Key Generation Path
To generate an API key, log in to your Odoo instance and navigate to:
**Preferences / My Profile → Account Security → New API Key**

### Transport & Deprecation Note
The client automatically detects if the native JSON-2 API is available at `/json/2/<model>/<method>` (which uses `Authorization: bearer <API_KEY>`) and falls back to XML-RPC if it is not. Please note that XML-RPC and JSON-RPC are deprecated in Odoo 19 and scheduled for removal in Odoo 22.

### Sources
- [Odoo 14.0 External API Documentation](https://www.odoo.com/documentation/14.0/developer/reference/external_api.html) (API keys introduction)
- [Odoo 19.0 External API Documentation](https://www.odoo.com/documentation/19.0/developer/reference/external_api.html) (JSON-2)
- [Odoo 19.0 External RPC API Documentation](https://www.odoo.com/documentation/19.0/developer/reference/external_rpc_api.html) (XML-RPC deprecation)
- [Odoo Standard & Extended Support Policy](https://www.odoo.com/documentation/19.0/administration/standard_extended_support.html) (Support timelines)

## Tools and Resources

The server exposes 14 tools and 2 resource types:

### Tools

1. `search_read`: Search and read records in one call (Odoo `search_read`).
2. `read_record`: Read one record by id, always with named fields.
3. `count_records`: Count the records matching a domain (Odoo `search_count`).
4. `instance_overview`: Summarise the connected instance: version, companies, volumes per area, in-house modules, anomalies.
5. `create_record`: Create a record, reusing an existing match when `unique_on` is given.
6. `write_record`: Write field values to one record and report what actually changed.
7. `run_action`: Run a workflow method and report the state it left behind.
8. `cancel_record`: Cancel a record through `action_cancel`, following the wizard it returns.
9. `notify_user`: Notify users on a record's chatter. Internal by default.
10. `create_activity`: Schedule an activity: the only notification that carries a deadline.
11. `download_docs`: Save every document of a record to disk, chatter files included.
12. `generate_pdf`: Render the PDF of a record and return where it was saved.
13. `explore_module`: Discover a module's structure by interrogating the live instance.
14. `list_known_modules`: List the modules this server has learned: name, generation date, records.

### Resources

* `odoo://skill`: Access the Odoo assistant skill instructions.
* `odoo://ref/*`: Access generated reference documentation for explored modules.

## Host Configuration Examples

### Claude Desktop
Add this to your `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "odoo-assistant": {
      "command": "uvx",
      "args": [
        "odoo-assistant"
      ],
      "env": {
        "ODOO_BASE_URL": "https://mycompany.odoo.com",
        "ODOO_DB": "mycompany",
        "ODOO_USER": "admin",
        "ODOO_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

### Cursor
Add this to your `.cursor/mcp.json` or configure it in the Cursor settings UI:
```json
{
  "mcpServers": {
    "odoo-assistant": {
      "command": "uvx",
      "args": [
        "odoo-assistant"
      ],
      "env": {
        "ODOO_BASE_URL": "https://mycompany.odoo.com",
        "ODOO_DB": "mycompany",
        "ODOO_USER": "admin",
        "ODOO_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

### VS Code Copilot
Add this to your VS Code `settings.json`:
```json
{
  "mcp.servers": {
    "odoo-assistant": {
      "command": "uvx",
      "args": [
        "odoo-assistant"
      ],
      "env": {
        "ODOO_BASE_URL": "https://mycompany.odoo.com",
        "ODOO_DB": "mycompany",
        "ODOO_USER": "admin",
        "ODOO_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

### ChatGPT (Custom Connectors)
To connect this server to ChatGPT via Custom Connectors:
1. Go to Settings → Connectors → Add Connector.
2. Enter the server URL or select from the Registry.
3. Enter your Odoo credentials when prompted.

### Hermes
Add the server using the Hermes CLI:
```bash
hermes mcp add odoo-assistant \
  --env ODOO_BASE_URL=https://mycompany.odoo.com \
  --env ODOO_DB=mycompany \
  --env ODOO_USER=admin \
  --env ODOO_API_KEY=your-api-key-here \
  --args run odoo-assistant
```

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

