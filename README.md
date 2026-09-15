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
* `ODOO_MCP_ALLOW`: Optional, default `*` — every method the deny list does not refuse. The single value `none` makes the server read-only, which is what you want when pointing an agent at live company data for reading. Anything else is a comma-separated list of method names. See "What the agent may do" below.
* `ODOO_MCP_DENY`: Optional. Unset, it is the default deny list — `unlink`, `archive`, `action_cancel`, `button_cancel`, `action_reverse`, `action_draft`, `mailing.mailing:action_send`. A value you set replaces that list entirely. See "What the agent may do" below.
* `ODOO_MCP_ALLOW_UNLINK`: Optional, off by default. `yes` is the only thing that grants `unlink`; no entry on either list can. See "What the agent may do" below.
* `ODOO_MCP_DATA_DIR`: Optional. Where this server keeps everything it writes, the instance profiles included. It defaults to the platform's own data directory — `%LOCALAPPDATA%\odoo-assistant` on Windows, `~/Library/Application Support/odoo-assistant` on macOS, and `$XDG_DATA_HOME/odoo-assistant` (else `~/.local/share/odoo-assistant`) elsewhere.

## What the agent may do

Every write and every action passes a gate before it reaches Odoo. The gate
judges a call by its METHOD NAME against two lists you own, in the host
configuration file, and reads both from the process environment at call time:

* `ODOO_MCP_ALLOW` — what may run. Unset or empty means `*`: every method the
  deny list does not refuse. The single value `none` makes the server
  read-only.
* `ODOO_MCP_DENY` — what may not. Unset or empty means the default list:
  `unlink`, `archive`, `action_cancel`, `button_cancel`, `action_reverse`,
  `action_draft`, `mailing.mailing:action_send`. A value you set **replaces**
  that list entirely rather than extending it, so the refusals in force are
  exactly the names in your own file — `ODOO_MCP_DENY=unlink` is how you
  re-admit `action_cancel`.

An entry is either `method`, which matches that method on every model, or
`model:method`, which matches it on one. Matching is exact string equality — no
prefix, no substring, so `action_send` never matches `action_send_and_print`
and `action_cancel` never matches `button_cancel`. A human read and approved
those exact names, and a looser match would let lookalikes through that nobody
saw. Deny is checked before allow, so a name on both lists refuses.

Model qualification is what the measured case needs. On one live instance 31
models answer to `action_send`, and only the Evolution wizards should — the one
on `mailing.mailing` can email an entire customer base in a single call. That
is why the default deny list spells that entry `mailing.mailing:action_send`
and leaves `action_send` working everywhere else.

Four rules sit outside the lists:

* **`unlink` is decided before both of them.** No value of `ODOO_MCP_ALLOW` or
  `ODOO_MCP_DENY` can ever grant deletion; only `ODOO_MCP_ALLOW_UNLINK=yes`
  does. Deletion is the one action that cannot be undone, and a name in a
  comma-separated list must never be enough to grant it.
* **`archive` is a virtual name.** Both `action_archive` and a `write` carrying
  `active: False` carry it into the lists, so denying `archive` refuses hiding
  records however they are spelled.
* **Private methods are always refused**, whatever the lists say. Odoo itself
  rejects every method starting with `_`, so no list could deliver one.
* **Reads never pass through either list**, because a read has no effect for a
  list to govern. The `account.move` structural guard is untouched by any of
  this and still applies to them: a query mixing invoices, bills and journal
  entries is refused whatever the lists hold, because a meaningless read is its
  own hazard.

| Configuration | What it permits |
|---|---|
| `ODOO_MCP_ALLOW=none` | Reads only. Nothing this server does can change a record — the setting for an agent pointed at live production data. |
| Nothing set | **Default.** Every method except the seven on the default deny list: creating, writing, confirming orders, posting invoices, scheduling activities, messaging users. |
| `ODOO_MCP_ALLOW_UNLINK=yes` | The default, plus `unlink`. The deny list is unchanged, so `archive` and `action_cancel` stay refused until you set `ODOO_MCP_DENY` yourself. |

The lists are set out of band, by a human, and the model running against this
server cannot change them. When the gate refuses, the reason names the call,
the entry that decided it and the variable that would change the answer, so the
agent can explain what the operation would have changed and leave the decision
to you.

Note that this is the authority of this server, not of the account. An agent
with shell access can always bypass an MCP server by invoking Odoo directly. A
limit that must hold regardless of the client belongs in the Odoo access rights
of the user the API key belongs to, where the Odoo server enforces it.

## Database and login: when you must set them

Only `ODOO_BASE_URL` and `ODOO_API_KEY` are required everywhere. `ODOO_DB` and
`ODOO_USER` are discovered, and whether that discovery can succeed depends on
how your instance is hosted.

The database is looked for in two steps, in this order: `list()` on
`/xmlrpc/db`, then a `/web/session/get_session_info` POST, which needs no
credentials and still reports the database name when `list_db = False` hides
the first one. The login is never asked for — an API key belongs to exactly one
user, and `execute_kw` accepts it only with that user's uid, so the client
finds the owner by probing `res.users` for uid 1 through 59.

| Hosting | `ODOO_DB` | `ODOO_USER` | Why |
|---|---|---|---|
| **Odoo Online** (`*.odoo.com`, SaaS) | **Required** | Optional | Measured: the database-list endpoint is disabled there, and without the name every tool call fails with an opaque "Error executing tool" that gives no hint the database is the problem. The SaaS name is not the subdomain — it carries a suffix, in the shape `mycompany16-prod-12345678`, and you find it at `/web/database/selector`. |
| **Odoo.sh** | Optional | Optional | A branch serves one database, and the session-info fallback reports its name. Untested against a live branch: set it if the first call fails. |
| **On-premise, one database** | Optional | Optional | Discovery returns the single name, from either step. |
| **On-premise, several databases** | Optional, but convenient | Optional | Not required: the discovery error **names the databases it found** and tells you to pick one, so the failure is self-explanatory. Setting it skips that round trip and removes the ambiguity. |

`ODOO_USER` is never mandatory by itself, on any hosting. Setting it saves up to
59 discovery round trips on the first call of a session, and it becomes
**required when the API key owner's uid is 60 or higher**, because discovery
only probes uid 1 to 59. It must be the login (e.g. `jane@mycompany.com`), and
a wrong value is quiet in a way that misleads: Odoo's `authenticate()` returns
`False` for an unknown login rather than raising, so a typo reads like a
permission error and not like a typo.

### By Odoo version

**Odoo 14 through 18 behave identically here.** XML-RPC carries the database
name in every `execute_kw` call, so the client must know it before it can
authenticate at all — which is exactly why discovery exists.

**Odoo 19** adds the JSON-2 API, which selects the database with an
`X-Odoo-Database` HTTP header. The header table on Odoo's own page lists it as
optional, and the page's *Database* section is specific about when it stops
being: it is "required when a single Odoo server hosts multiple databases and
the `dbfilter` wasn't configured to use the `Host` header", or, as the same page
puts it elsewhere, the database "must only be provided (via the
`X-Odoo-Database` HTTP header) on systems where there are multiple databases
available for a same domain". Where the hostname already picks the database —
Odoo Online, Odoo.sh, any `dbfilter` keyed on `Host` — it can be left out.

This server's client tries JSON-2 first: one `POST
/json/2/res.users/search_count` carrying `Authorization: Bearer <key>`, and a
200 makes JSON-2 the transport for the session; anything else falls back to
XML-RPC. It sends `Authorization` and `Content-Type` and nothing else, so it
does not set `X-Odoo-Database` — over JSON-2 the host has to resolve the
database itself, and `ODOO_DB` reaches only the XML-RPC path.

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
| **19.0** | Yes | Yes | **Yes** (until Sep 2028) | Protocol-compatible. Untested against a live instance. API keys require description and expiry (max 3 months). The JSON-2 API selects the database with an `X-Odoo-Database` header — see "Database and login: when you must set them" above. |

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
the database, which discovery cannot reach on Odoo Online. The login is
discovered, and the gate keeps its defaults unless you add `ODOO_MCP_ALLOW` or
`ODOO_MCP_DENY` — see "What the agent may do". Note the quotes: environment
values are strings.

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
and restart after editing. Anything you change here, the allow and deny lists
included, takes effect only on the next launch.

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
If you are connecting to a production instance hosted on Odoo Online (SaaS), you must set `ODOO_DB` and should set `ODOO_MCP_ALLOW` to `"none"` for safety. Here is how it looks in Claude Desktop:

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
        "ODOO_MCP_ALLOW": "none"
      }
    }
  }
}
```

Setting `ODOO_DB` is mandatory to bypass the disabled database-list endpoint on Odoo Online, while `ODOO_MCP_ALLOW` set to `"none"` ensures the agent cannot modify live production data.

The examples omit the optional variables. Set `ODOO_DB` when the instance serves several databases, `ODOO_USER` to skip the uid probe, and `ODOO_MCP_ALLOW` / `ODOO_MCP_DENY` when the gate's defaults — every method but the seven denied ones — are not what you want.

## Changelog

What changed in each release is in [CHANGELOG.md](https://github.com/singleflo/odoo-assistant-mcp/blob/main/CHANGELOG.md), kept there rather than repeated here so the two cannot drift.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
