# Install Odoo Assistant — instructions for the AI agent reading this

You were given this URL by a person who wants Odoo connected to you. Work
through the steps below in order and do the work yourself: they should not have
to edit a file by hand. What is not spelled out here is in the README, which
each step links into:
<https://github.com/singleflo/odoo-assistant-mcp/blob/main/README.md>

## 0. Ask the person three things, and only three

1. **The Odoo URL** — for example `https://mycompany.odoo.com`, no trailing
   slash.
2. **An API key — never a password.** This server accepts no password, by
   design. Tell them where to create a key: in Odoo, **Preferences (My
   Profile) → Account Security → New API Key**. On Odoo 19 the key also needs
   a description and an expiry of at most three months. Keys exist from Odoo
   14 on; on anything older this server cannot authenticate at all.
3. **Whether it is Odoo Online** — an instance Odoo hosts for them at a
   `*.odoo.com` address. Their answer decides step 4.

Ask for nothing else. The login is discovered from the key, and every other
setting has a working default.

## 1. Detect the OS and the host you are running in

Find the operating system first:

- macOS or Linux: `uname -s` prints `Darwin` or `Linux`.
- Windows PowerShell: `$env:OS` prints `Windows_NT`.

Then work out which application you are running inside, because it decides
which file you write in step 3. Check which of these exist:

| Host | Look for |
|---|---|
| Claude Code | the `claude` command on `PATH`, `~/.claude.json`, or `.mcp.json` in the project |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows) |
| OpenAI Codex CLI | `~/.codex/config.toml` |
| opencode | `~/.config/opencode/opencode.json` or `.jsonc`, or `opencode.json` in the project |
| Hermes | `~/.hermes/config.yaml` |
| Cursor | `~/.cursor/mcp.json` or `.cursor/mcp.json` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` |
| VS Code / GitHub Copilot | `.vscode/mcp.json` |
| Gemini CLI | `~/.gemini/settings.json` |
| Zed | `~/.config/zed/settings.json`; if it is not there, have them open it from the Command Palette with `zed: open settings file` |

If several match, or none does, **ask the person which application they are
talking to you in**. Do not guess. A correct entry written into the wrong
file gives them a server that never starts and no error to read.

## 2. Make sure `uv` is installed

The server runs as `uvx odoo-assistant`, so `uvx` has to exist. Run
`uvx --version` first; if it prints a version, go to step 3. Otherwise install
uv — macOS and Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Source: <https://docs.astral.sh/uv/getting-started/installation/>

Then confirm with `uvx --version` again, in a **new** shell — the installer
puts `uv` on the `PATH` of shells started after it, not the one you are in.

## 3. Write the host configuration

Fourteen hosts have a snippet in the README, each checked against that host's
own documentation:
[Host Configuration Examples](https://github.com/singleflo/odoo-assistant-mcp/blob/main/README.md#host-configuration-examples).
Anchors follow the heading, lowercased and hyphenated — `#openai-codex-cli`,
`#hermes`, `#cursor`, `#windsurf`, `#vs-code-and-github-copilot`,
`#gemini-cli`, `#cline`, `#roo-code`, `#zed`, `#jetbrains-ai-assistant`. Read
the section before you write: the shapes differ in ways the hosts reject
outright — Zed's key is `context_servers`, VS Code's is `servers`, opencode's
is `mcp`, and Hermes needs an absolute path to `uvx`. Merge into the existing
file rather than overwriting it — they usually have other servers configured
already. The three most common hosts, in full:

**[Claude Desktop](https://github.com/singleflo/odoo-assistant-mcp/blob/main/README.md#claude-desktop)** —
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS,
`%APPDATA%\Claude\claude_desktop_config.json` on Windows:

```json
{
  "mcpServers": {
    "odoo-assistant": {
      "command": "uvx",
      "args": ["odoo-assistant"],
      "env": {
        "ODOO_BASE_URL": "https://mycompany.odoo.com",
        "ODOO_API_KEY": "the-key-they-gave-you"
      }
    }
  }
}
```

**[Claude Code](https://github.com/singleflo/odoo-assistant-mcp/blob/main/README.md#claude-code)** —
one command writes it. Keep `--transport stdio` between the last `--env` and
the server name: `--env` keeps reading `KEY=value` pairs, so a name directly
after it is read as another pair and rejected.

```bash
claude mcp add --env ODOO_BASE_URL=https://mycompany.odoo.com \
  --env ODOO_API_KEY=the-key-they-gave-you \
  --transport stdio odoo-assistant -- uvx odoo-assistant
```

**[opencode](https://github.com/singleflo/odoo-assistant-mcp/blob/main/README.md#opencode)** —
`~/.config/opencode/opencode.json`. The key is `mcp`, `command` is one array,
the block is `environment`, and `timeout` defaults to 5000 ms, which the first
call overruns:

```json
{
  "mcp": {
    "odoo-assistant": {
      "type": "local",
      "enabled": true,
      "command": ["uvx", "odoo-assistant"],
      "timeout": 120000,
      "environment": {
        "ODOO_BASE_URL": "https://mycompany.odoo.com",
        "ODOO_API_KEY": "the-key-they-gave-you"
      }
    }
  }
}
```

## 4. Decide `ODOO_DB`

If they said **yes, Odoo Online**: `ODOO_DB` is required. Odoo Online disables
the database-list endpoint, so the server cannot discover the name, and every
tool call fails with an opaque "Error executing tool" that never mentions the
database. Ask them for it, and tell them where to look: open
`<their-url>/web/database/selector`, or the Odoo.com account page. Warn them
it is **not** the pretty subdomain — it carries a suffix, in the shape
`mycompany16-prod-12345678`. Add it to the `env` block.

Otherwise **leave `ODOO_DB` out**, and `ODOO_USER` with it: both are
discovered. If the instance serves several databases the error names them, and
you can add it then — see
[Database and login: when you must set them](https://github.com/singleflo/odoo-assistant-mcp/blob/main/README.md#database-and-login-when-you-must-set-them).

## 5. Decide what the server may change

Ask one question: **is this live production data they only want read?**

- **Yes** — add `"ODOO_MCP_ALLOW": "none"` to the `env` block. The server
  becomes read-only: nothing it does can alter a record.
- **No, or a test instance** — set neither `ODOO_MCP_ALLOW` nor
  `ODOO_MCP_DENY`. The defaults already refuse `unlink`, `archive`,
  `action_cancel`, `button_cancel`, `action_reverse`, `action_draft` and
  `mailing.mailing:action_send`.

**Never set `ODOO_MCP_ALLOW_UNLINK`.** It is the only thing that grants record
deletion, deletion is the one action nobody can undo, and it is not yours to
turn on. If the person asks for it, tell them where it is documented and let
them write it themselves:
[What the agent may do](https://github.com/singleflo/odoo-assistant-mcp/blob/main/README.md#what-the-agent-may-do).

## 6. Restart the host, then verify

The host reads its configuration at startup and does not reload it. Restart it
fully — for Claude Desktop, quit the application rather than closing the
window. Then call `instance_overview`. A summary of their instance — version,
companies, record volumes — means the install works; tell them the version and
company names you got back, which proves you reached *their* Odoo. Expect that
first call to be slow: the server connects in a background thread at startup,
but a call arriving before it finishes waits for it, and `instance_overview`
itself makes dozens of round trips. Later calls are fast.

If it times out, raise the host's timeout — the key differs per host, and the
README section for each gives it:

- opencode: `timeout`, milliseconds, default 5000. Raise to 120000.
- Claude Code: `timeout`, milliseconds, per server.
- Codex CLI: `startup_timeout_sec` and `tool_timeout_sec`, **seconds**.
- Hermes: `timeout` and `connect_timeout`, **seconds**.
- Roo Code: `timeout`, **seconds**, 1 to 3600.
- Gemini CLI: `timeout`, milliseconds, already 600000 by default.
- Cline: no key in the file — it is a setting in the MCP servers panel.

If it fails another way, the message names the variable at fault. Re-read
step 4 before changing anything else: a missing `ODOO_DB` is the usual cause.

## 7. Tell the person what you did

Close with four things, plainly:

1. **Which file you wrote**, by full path.
2. **Which variables you set** — name them and their values, API key redacted.
3. **That the API key sits in plain text in that file.** Anyone who can read
   the file, or any backup or repository it reaches, has their key. Say so
   explicitly if the file is inside a git repository. A key can be revoked on
   its own in Odoo, under Account Security.
4. **What the server may do** — read-only if you set `ODOO_MCP_ALLOW=none`,
   otherwise reads plus writes with the seven default refusals, and never
   deletion.
