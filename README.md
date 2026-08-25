<!-- mcp-name: io.github.singleflo/odoo-assistant -->
# Odoo Assistant MCP Server

An Odoo virtual employee via the Model Context Protocol (MCP). This server exposes Odoo's business logic, records, and workflows to LLMs, allowing them to query, create, update, and act on Odoo data safely.

## Quickstart

### 1. Install

Run the server directly:

```bash
uvx odoo-assistant
```

Or install it into your environment:

```bash
uv pip install odoo-assistant
```

Installing from source for development remains possible:

```bash
uv pip install git+https://github.com/singleflo/odoo-assistant-mcp
```

### 2. Configure Environment Variables

* `ODOO_BASE_URL`: Mandatory always. The base URL of your Odoo instance, with no trailing slash (e.g., `https://mycompany.odoo.com`).
* `ODOO_API_KEY`: Mandatory always. The Odoo API key (Odoo 14+, generate under Settings > Users > API Keys > New). An account password is not accepted. A key is per-user, scoped, and revocable on its own. Odoo 19 additionally requires a description and an expiry, max 3 months.
* `ODOO_DB`: Mandatory on Odoo Online (SaaS, `*.odoo.com`), optional elsewhere. On Odoo Online, the database-list endpoint is disabled. Discovery cannot find the name, and every tool call fails with an opaque "Error executing tool" without hinting that the database is the problem. With `ODOO_DB` set, the same config connects immediately. The SaaS database name is not the pretty subdomain — it carries a suffix, in the shape `mycompany16-prod-12345678` — and you find it at `/web/database/selector` or in the Odoo.com account page. Elsewhere, it is discovered automatically when the instance serves exactly one database, and is required when it serves several.
* `ODOO_USER`: Never mandatory. Omitted, the client probes `res.users` for uid 1 to 59 and keeps the one the key answers for. This adds up to 59 extra round trips on the first call, and it fails outright if the key owner's uid is 60 or higher. Setting it removes that cost. It must be the login (e.g. `jane@mycompany.com`), and a wrong value makes Odoo's `authenticate()` return False rather than raise — which reads like a permission error.
* `ODOO_MCP_MAX_LEVEL`: Optional, default `3`. The highest safety level this server may execute, `0` to `4`. A value of `0` makes the server refuse every write, which is what you want when pointing an agent at live company data for reading. Refer to the Safety Layer section for the L0 to L5 table. An invalid value refuses startup. `5` is accepted but identical to `4`.
* `ODOO_MCP_PROTECTED_HOSTS`: Comma-separated hosts this server refuses to write to (empty by default, no host is baked into the package). A listed host still allows reads. Writing to it needs `ODOO_ALLOW_PROD_WRITE=yes` as a deliberate override.

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

### Choosing the ceiling

`ODOO_MCP_MAX_LEVEL` sets the highest level this server may execute. Each value
is cumulative, permitting its own level and everything below:

| Value | What it permits |
|---|---|
| `0` | Reads only. |
| `1` | + single-record writes and creations. |
| `2` | + batches above 5 records. |
| `3` | **Default.** + confirming orders, posting invoices, sending mail. |
| `4` | + `unlink`, `action_cancel`, archiving. |
| `5` | Accepted, but identical to `4` in effect. See below. |

Two behaviours are worth knowing before you pick a number:

* **`5` does not unlock L5.** Both L5 variants are refused before the ceiling is
  ever read. `L5_PRIVATE` is refused because Odoo itself rejects every method
  starting with `_`, so no ceiling could deliver it; `L5_UNKNOWN` is refused
  because a method nobody classified has, by definition, unreviewed effects. The
  way to allow such a method is to add it to `WRITE_L1`/`L3`/`L4` in
  `safety_layer.py` in code, reviewed, never through configuration.
* **An invalid value refuses startup.** `ODOO_MCP_MAX_LEVEL="O"` raises rather
  than falling back to the default, because the fallback is write-capable: a
  typo must not hand you a writing server you believed was read-only.

The ceiling is set out of band, by a human, and read from the process
environment at startup. The model running against this server cannot raise it;
when a call exceeds the ceiling the refusal names the level required, so the
agent can explain what the operation would change and leave the decision to you.

Note that this is the authority of this server, not of the account. An agent
with shell access can always bypass an MCP server by invoking Odoo directly. A
limit that must hold regardless of the client belongs in the Odoo access rights
of the user the API key belongs to, where the Odoo server enforces it.

## Odoo Version Support

Odoo 14.0 is the absolute minimum supported version because this server authenticates using API keys only, which do not exist in Odoo 13 or earlier.

| Odoo Version | API Keys | XML-RPC | Officially Maintained (Aug 2026) | Support Level / Notes |
|---|---|---|---|---|
| **≤ 13.0** | **No** | Yes | No | **Unsupported**. API keys do not exist, so this server cannot authenticate. |
| **14.0** | **Yes** | Yes | No | Protocol-compatible. Untested against a live instance. |
| **15.0** | Yes | Yes | No | Protocol-compatible. Untested against a live instance. |
| **16.0** | Yes | Yes | No | **Verified against a live Enterprise instance**: connection, authentication, reads, `instance_overview` and the Discuss tools. Two generational differences are handled for you — see the note below. Write scenarios were not exercised. |
| **17.0** | Yes | Yes | **Yes** (until Sep 2026) | Protocol-compatible. Untested against a live instance. |
| **18.0** | Yes | Yes | **Yes** (until Sep 2027) | **Primary target**. Verified and fully supported against a live instance. |
| **19.0** | Yes | Yes | **Yes** (until Sep 2028) | Protocol-compatible. Untested against a live instance. API keys require description and expiry (max 3 months). |

Two things changed between Odoo 16 and 17, and neither needs configuration:

* Discuss was renamed. `mail.channel` / `mail.channel.member` became
  `discuss.channel` / `discuss.channel.member` in 17. The server asks the
  instance which pair it has and uses that, so the four Discuss tools work on
  both generations.
* Subscriptions moved onto `sale.order`, which before 17 had no
  `subscription_state` field at all. On 16 that section is simply absent from
  `instance_overview` — an absence, not a failure.

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

The server exposes 19 tools and 2 resource types:

### Tools

1. `search_read`: Search and read records in one call (Odoo `search_read`).
2. `read_record`: Read one record by id, always with named fields.
3. `count_records`: Count the records matching a domain (Odoo `search_count`).
4. `instance_overview`: Summarise the connected instance: version, companies, volumes per area, in-house modules, anomalies.
5. `required_fields`: List what Odoo demands before a `create` on a model, the default it would apply, and how existing records actually use it.
6. `create_record`: Create a record, reusing an existing match when `unique_on` is given.
7. `write_record`: Write field values to one record and report what actually changed.
8. `run_action`: Run a workflow method and report the state it left behind.
9. `cancel_record`: Cancel a record through `action_cancel`, following the wizard it returns.
10. `notify_user`: Notify users on a record's chatter. Internal by default.
11. `create_activity`: Schedule an activity: the only notification that carries a deadline.
12. `download_docs`: Save every document of a record to disk, chatter files included.
13. `generate_pdf`: Render the PDF of a record and return where it was saved.
14. `list_message_targets`: List who can be messaged and where, including internal users with presence (online/away/offline) and the caller's open conversations. Ask this before sending.
15. `read_conversation`: Read a Discuss conversation, newest first.
16. `send_direct_message`: Send a 1-to-1 Discuss message that appears in the user's chat systray in real time. This sends no email and reaches them whatever their notification setting says.
17. `send_channel_message`: Post to an existing Discuss channel, refusing a room that holds a non-employee.
18. `explore_module`: Discover a module's structure by interrogating the live instance.
19. `list_known_modules`: List the modules this server has learned: name, generation date, records.

Tools 10-11 (`notify_user`, `create_activity`) notify ABOUT a record and land
in the Inbox bell; tools 14-17 are Discuss conversations that land in the chat
systray. "Message user X" is the second kind, which uses `send_direct_message`, not
`notify_user`.

### Resources

* `odoo://skill`: Access the Odoo assistant skill instructions.
* `odoo://ref/*`: Access generated reference documentation for explored modules.

## Host Configuration Examples

Every example below carries only what matters: the two required variables, and
the ceiling, which is the setting that decides whether this server can write, made
visible in the file the human owns. The database and the login are discovered,
and `3` is the ceiling's default. Note the quotes: environment values are
strings.

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
        "ODOO_API_KEY": "your-api-key-here",
        "ODOO_MCP_MAX_LEVEL": "3",
        "ODOO_DB": "your-database-name"
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
        "ODOO_API_KEY": "your-api-key-here",
        "ODOO_MCP_MAX_LEVEL": "3",
        "ODOO_DB": "your-database-name"
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
        "ODOO_API_KEY": "your-api-key-here",
        "ODOO_MCP_MAX_LEVEL": "3",
        "ODOO_DB": "your-database-name"
      }
    }
  }
}
```

### opencode
Add this to `opencode.json` or `.opencode/opencode.json` in your project, or to
`~/.config/opencode/opencode.json` to make the server available everywhere:
```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "odoo-assistant": {
      "type": "local",
      "enabled": true,
      "command": [
        "uvx",
        "odoo-assistant"
      ],
      "timeout": 120000,
      "environment": {
        "ODOO_BASE_URL": "https://mycompany.odoo.com",
        "ODOO_API_KEY": "your-api-key-here",
        "ODOO_MCP_MAX_LEVEL": "3",
        "ODOO_DB": "your-database-name"
      }
    }
  }
}
```

opencode's shape differs from the hosts above in ways it rejects outright. The
key is `mcp` (not `mcpServers`), `type` is required, `command` is a single array
holding the program and its arguments (there is no separate `args`), and the
environment block is `environment` (not `env`).

Set `timeout` deliberately. It defaults to **5000 ms**. The first call of a
session pays for authentication plus, for `instance_overview`, dozens of XML-RPC
round trips, which easily exceeds five seconds against a real instance. Set it to 120000.

opencode reads its config once at startup and does not hot-reload it. Quit
and restart after editing. Anything you change here, the ceiling included,
takes effect only on the next launch.

### ChatGPT (Custom Connectors)
To connect this server to ChatGPT via Custom Connectors:
1. Go to Settings → Connectors → Add Connector.
2. Enter the server URL or select from the Registry.
3. Enter your Odoo credentials when prompted.

### Hermes
Hermes keeps its servers in **YAML**, under `mcp_servers:` in `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  odoo-assistant:
    command: /Users/you/.local/bin/uvx
    args:
      - odoo-assistant
    env:
      ODOO_BASE_URL: https://mycompany.odoo.com
      ODOO_API_KEY: your-api-key-here
      ODOO_DB: your-database-name
      ODOO_MCP_MAX_LEVEL: "3"
    timeout: 120000
    connect_timeout: 60
    enabled: true
```

Three details this shape does not forgive. `command` is a **string** and takes
only the program, with the arguments in a separate `args` list — the opposite of
opencode's single array. The environment block is `env`. And the command needs
an **absolute path**: Hermes runs as a desktop application, which does not
inherit the `PATH` of your shell, so a bare `uvx` is not found.

`hermes mcp add` can write this entry for you, but pass `--args` **last**: it is
greedy and swallows every flag that follows it, landing `--env` pairs inside
`args` and leaving the server to start with no credentials at all.

### Odoo Online Production (Read-Only Example)
If you are connecting to a production instance hosted on Odoo Online (SaaS), you must set `ODOO_DB` and should set `ODOO_MCP_MAX_LEVEL` to `"0"` for safety. Here is how it looks in Claude Desktop:

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
        "ODOO_API_KEY": "your-api-key-here",
        "ODOO_DB": "mycompany16-prod-12345678",
        "ODOO_MCP_MAX_LEVEL": "0"
      }
    }
  }
}
```

Setting `ODOO_DB` is mandatory to bypass the disabled database-list endpoint on Odoo Online, while `ODOO_MCP_MAX_LEVEL` set to `"0"` ensures the agent cannot modify live production data.

The examples omit the optional variables. Set `ODOO_DB` when the instance serves several databases, `ODOO_USER` to skip the uid probe, and `ODOO_MCP_MAX_LEVEL` to change the ceiling from its default of `3`.

## Changelog

What changed in each release is in [CHANGELOG.md](https://github.com/singleflo/odoo-assistant-mcp/blob/main/CHANGELOG.md), kept there rather than repeated here so the two cannot drift.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
