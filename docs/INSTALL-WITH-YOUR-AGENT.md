# Install Singleflo for Odoo — instructions for the AI agent reading this

You were given this URL by a person who wants their Odoo connected to you.
Assume they are **not technical**: they may never have opened a terminal, do
not know what MCP, `uv`, YAML or an environment variable is, and should not
have to learn. You do the technical work; they only answer questions and click
inside Odoo.

Work through the steps below in order. What is not spelled out here is in the
README, which each step links into:
<https://github.com/singleflo/odoo-assistant-mcp/blob/main/README.md>

## How to talk to the person

- **Use their language.** If they wrote to you in Italian, Spanish, Czech or
  anything else, every message you send them is in that language. Odoo's menu
  labels are translated in their interface too: give the English label and,
  if you know it, the one their Odoo shows.
- **One small step per message.** Say what to click, then wait for them to
  say it is done (or to describe what they see) before the next step. Never
  paste the whole procedure in one block.
- **No jargon.** Say "the settings file of this app", not "the host config";
  "a key that lets me read your Odoo", not "an API credential". Run commands
  yourself; never ask them to type one unless you have no way to run it.
- **Never ask for their Odoo password.** This server does not accept one. The
  only moment the password is typed is inside Odoo, in step 2.5 below, to
  confirm it is them — it is never typed to you.
- If something fails, explain it in one plain sentence and say what you will
  try next. Do not show them a stack trace.

## 0. Can you run commands on this computer?

This install runs a small program on the person's computer, so you need to be
able to run shell commands and write files.

- **Yes** (Hermes, Claude Code, Claude Desktop with a shell tool, Codex CLI,
  opencode, Cursor, Windsurf, Gemini CLI and similar) — go to step 1.
- **No** — you are a chat in a browser or a phone app (Claude.ai, ChatGPT)
  and cannot touch their computer. Do not try. Use the **hosted** route, where
  nothing is installed:
  - **Claude.ai** (web, Desktop, mobile): walk them through **Settings or
    Customize → Connectors → Add custom connector**, name *Singleflo for
    Odoo*, URL `https://mcp.singleflo.com/mcp`. Or give them this link, which
    opens the same dialog prefilled:
    `https://claude.ai/customize/connectors?modal=add-custom-connector&connectorName=Singleflo%20for%20Odoo&connectorUrl=https%3A%2F%2Fmcp.singleflo.com%2Fmcp`
  - **ChatGPT**: **Settings → Security and login → Developer mode** on, then
    at <https://chatgpt.com/plugins> the plus button, MCP URL
    `https://mcp.singleflo.com/mcp`. Developer mode needs a Plus, Pro,
    Business, Enterprise or Education account.

  In both, the first use opens a Singleflo sign-in page that asks for their
  Odoo address, an API key (step 2 below explains how to make one), the
  database name if they are on Odoo Online (step 3), and **read** or
  **standard** access. They type the key **into that page, not into the
  chat**. Then you are done; skip to step 8.

## 1. Ask the Odoo address

Ask: *"What address do you open in the browser to use Odoo?"* — for example
`https://mycompany.odoo.com` or `https://erp.mycompany.com`. Keep only the
scheme and host: drop anything from `/web` or `/odoo` on, and any trailing
slash. If they are unsure, ask them to open Odoo and copy what the address bar
shows; you clean it up.

From the address you can already tell whether it is **Odoo Online** (Odoo
hosts it for them): the address ends in `.odoo.com`. If it does not, ask once
— *"Is your Odoo hosted by Odoo itself, on odoo.com?"* — because an Odoo
Online instance on its own domain looks the same from outside. The answer
decides step 3.

## 2. Walk them through creating an API key

Explain first, in one or two sentences: the key is like a password made only
for me; it can only do what their own Odoo user can do; they can delete it in
Odoo at any time and I stop working immediately. Odoo shows it **only once**.

Keys exist from Odoo 14 on. If their Odoo is 13 or older, stop here and tell
them this server cannot connect to it.

Then guide them **one step per message**, waiting for each to be done. An
illustrated version of the same steps, with a screenshot of each, is at
<https://singleflo.com/en/odoo-assistant#api-key> (also `/it/`, `/es/`, `/cs/`
— pick their language); offer it if they prefer to follow pictures.

1. In Odoo, click **your picture or initials** in the top-right corner.
2. In the menu that opens, click **Preferences** (some versions say **My
   Profile**).
3. A window opens. Click the **Account Security** tab, next to *Preferences*.
4. Scroll to the **API Keys** section and click **New API Key**.
5. Odoo asks for **their own Odoo password** to confirm it is them. They type
   it there, in Odoo, and click **Confirm Password**. Remind them it stays in
   Odoo: you never see it and never need it.
6. They name the key — *Singleflo for Odoo* — and choose a **duration**. Say
   that when it runs out the connection stops and they will need a new key,
   so a longer duration means fewer renewals. On Odoo 19 the longest allowed
   is 3 months; before Odoo 17 there is no duration field at all. Then
   **Generate key**.
7. The key appears (about 40 letters and digits). They copy it **now** —
   once the window closes, Odoo cannot show it again; a lost key is simply
   replaced by a new one.

If they cannot find **API Keys** at all, their user may lack access to the
Preferences window, or the Odoo is older than 14. Ask them to send a
screenshot of what they see, or to ask whoever manages their Odoo.

Then ask them to paste the key to you. Tell them honestly that it will pass
through this chat and be written into a settings file on this computer, and
that they can delete it in Odoo whenever they want. If they would rather not
paste it, write the file with `PASTE-YOUR-KEY-HERE` in its place, show them
exactly which file and line to edit, and wait for them to say it is done.

## 3. Find the database name — Odoo Online only

Skip this step unless step 1 established Odoo Online.

On Odoo Online the server cannot discover the database name, and without it
every request fails with an opaque "Error executing tool" that never mentions
the database. It is **not** the pretty subdomain: it carries a suffix, in the
shape `mycompany16-prod-12345678`.

Help them find it, easiest first:

1. Ask them to open `<their-address>/web/database/selector` in the browser
   and read you the name shown there.
2. Or to sign in at <https://www.odoo.com/my/databases> and read you the
   database name listed for their instance.

Write it down for step 5 as `ODOO_DB`. For any instance that is **not** Odoo
Online, leave `ODOO_DB` out, and `ODOO_USER` with it — both are discovered. If
the instance serves several databases, the error you get in step 7 lists them;
add the right one then. Details:
[Database and login: when you must set them](https://github.com/singleflo/odoo-assistant-mcp/blob/main/README.md#database-and-login-when-you-must-set-them).

## 4. Decide what the server may change

Ask one plain question: *"Do you want me to only read your Odoo, or also
create and update things — quotes, contacts, activities, messages — when you
ask me to?"*

- **Only read** — add `ODOO_MCP_ALLOW` with value `none`. Nothing the server
  does can alter a record.
- **Also create and update** — set neither `ODOO_MCP_ALLOW` nor
  `ODOO_MCP_DENY`. The defaults already refuse `unlink`, `archive`,
  `action_cancel`, `button_cancel`, `action_reverse`, `action_draft` and
  `mailing.mailing:action_send`.

Recommend **only read** if they hesitate or the data is live production.

**Never set `ODOO_MCP_ALLOW_UNLINK`.** It is the only thing that grants record
deletion, deletion is the one action nobody can undo, and it is not yours to
turn on. If they ask for it, point them to
[What the agent may do](https://github.com/singleflo/odoo-assistant-mcp/blob/main/README.md#what-the-agent-may-do)
and let them write it themselves.

## 5. Detect the OS and the app you are running in

Operating system:

- macOS or Linux: `uname -s` prints `Darwin` or `Linux`.
- Windows PowerShell: `$env:OS` prints `Windows_NT`.

Then the application you are running inside, because it decides which file
you write in step 6. Check which of these exist:

| App | Look for |
|---|---|
| Hermes | the `hermes` command on `PATH`, or `~/.hermes/config.yaml` |
| Claude Code | the `claude` command on `PATH`, `~/.claude.json`, or `.mcp.json` in the project |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows) |
| OpenAI Codex CLI | `~/.codex/config.toml` |
| opencode | `~/.config/opencode/opencode.json` or `.jsonc`, or `opencode.json` in the project |
| Cursor | `~/.cursor/mcp.json` or `.cursor/mcp.json` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` |
| VS Code / GitHub Copilot | `.vscode/mcp.json` |
| Gemini CLI | `~/.gemini/settings.json` |
| Zed | `~/.config/zed/settings.json`; if it is not there, have them open it from the Command Palette with `zed: open settings file` |

You usually know which app you are; trust that first. If several match and
you are unsure, or none does, **ask the person which application they are
talking to you in**. A correct entry written into the wrong file gives them a
server that never starts and no error to read.

## 6. Make sure `uv` is installed

The server runs as `uvx odoo-assistant`, so `uvx` has to exist. Run
`uvx --version`; if it prints a version, go on. Otherwise install uv —
macOS and Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Source: <https://docs.astral.sh/uv/getting-started/installation/>

The installer puts `uv` on the `PATH` of shells started **after** it, not the
one you are in. Check it by its full path instead: `~/.local/bin/uvx
--version` on macOS and Linux, `& "$env:USERPROFILE\.local\bin\uvx.exe"
--version` on Windows. If the installer printed a different directory, use
that one.

Find the **absolute path** of `uvx` now — some apps need it in step 7:
`command -v uvx` (macOS, Linux), `(Get-Command uvx).Source` (Windows), or the
full path you just used.

## 7. Write the app's settings, restart it, verify

Merge into the existing file rather than overwriting it — it usually holds
other servers already. Back it up first (copy it next to itself with a
`.bak` suffix). Use the values from steps 1–4: `ODOO_BASE_URL`,
`ODOO_API_KEY`, `ODOO_DB` only if step 3 applied, `ODOO_MCP_ALLOW` only if
step 4 said *only read*.

### Hermes

`~/.hermes/config.yaml`, under `mcp_servers:`. Create the file if it does
not exist, and the `mcp_servers:` key if the file lacks it:

```yaml
mcp_servers:
  odoo-assistant:
    command: /Users/you/.local/bin/uvx   # the ABSOLUTE path from step 6
    args:
      - odoo-assistant
    env:
      ODOO_BASE_URL: https://mycompany.odoo.com
      ODOO_API_KEY: the-key-they-gave-you
      ODOO_DB: mycompany16-prod-12345678   # only on Odoo Online
      ODOO_MCP_ALLOW: "none"              # only if they chose "only read"
    timeout: 120
    connect_timeout: 60
    enabled: true
```

`command` is a **string** holding only the program, with the arguments in
the separate `args` list, and it must be an **absolute path** — a bare `uvx`
is not found. Both timeouts are in **seconds**. Keep `"none"` quoted. Then
run `/reload-mcp` in Hermes, or tell them to type it, rather than restarting.
Prefer editing the file over `hermes mcp add`: that command's `--args` takes
the rest of the line, so anything written after it is swallowed into `args`
and the server starts with no credentials.
[README section](https://github.com/singleflo/odoo-assistant-mcp/blob/main/README.md#hermes).

### Claude Code

One command writes it. Keep `--transport stdio` between the last `--env` and
the server name: `--env` keeps reading `KEY=value` pairs, so a name directly
after it is read as another pair and rejected. Add `--env ODOO_DB=…` and
`--env ODOO_MCP_ALLOW=none` the same way when they apply.

```bash
claude mcp add --scope user \
  --env ODOO_BASE_URL=https://mycompany.odoo.com \
  --env ODOO_API_KEY=the-key-they-gave-you \
  --transport stdio odoo-assistant -- uvx odoo-assistant
```

Then they start a new Claude Code session (`/exit`, then `claude` again).
[README section](https://github.com/singleflo/odoo-assistant-mcp/blob/main/README.md#claude-code).

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS,
`%APPDATA%\Claude\claude_desktop_config.json` on Windows. Use the absolute
`uvx` path here too: like Hermes, a desktop app does not see the shell's
`PATH`.

```json
{
  "mcpServers": {
    "odoo-assistant": {
      "command": "/Users/you/.local/bin/uvx",
      "args": ["odoo-assistant"],
      "env": {
        "ODOO_BASE_URL": "https://mycompany.odoo.com",
        "ODOO_API_KEY": "the-key-they-gave-you"
      }
    }
  }
}
```

Then they must **quit** Claude Desktop completely (menu → Quit, not just
closing the window) and reopen it.
[README section](https://github.com/singleflo/odoo-assistant-mcp/blob/main/README.md#claude-desktop).

### opencode

`~/.config/opencode/opencode.json`. The key is `mcp`, `command` is one
array, the block is `environment`, and `timeout` defaults to 5000 ms, which
the first call overruns:

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

Then restart opencode. [README section](https://github.com/singleflo/odoo-assistant-mcp/blob/main/README.md#opencode).

### Any other app

The README has a snippet for each, checked against that app's own
documentation:
[Host Configuration Examples](https://github.com/singleflo/odoo-assistant-mcp/blob/main/README.md#host-configuration-examples).
Anchors follow the heading, lowercased and hyphenated — `#openai-codex-cli`,
`#cursor`, `#windsurf`, `#vs-code-and-github-copilot`, `#gemini-cli`,
`#cline`, `#roo-code`, `#zed`, `#jetbrains-ai-assistant`. Read the section
before you write: the shapes differ in ways the apps reject outright — Zed's
key is `context_servers`, VS Code's is `servers`.

### Verify

The app reads its settings at startup. After the reload or restart, call
`instance_overview`. A summary of their instance — version, companies,
record volumes — means it works; tell them the Odoo version and the company
names you got back, which proves you reached *their* Odoo. If the restart
ended this conversation, tell them beforehand to come back and ask you
*"show me an overview of my Odoo"*.

Expect the first call to be slow: it authenticates and makes dozens of round
trips. Later calls are fast. If it times out, raise the app's timeout:

- Hermes: `timeout` and `connect_timeout`, **seconds**.
- opencode: `timeout`, milliseconds, default 5000. Raise to 120000.
- Claude Code: `timeout`, milliseconds, per server.
- Codex CLI: `startup_timeout_sec` and `tool_timeout_sec`, **seconds**.
- Roo Code: `timeout`, **seconds**, 1 to 3600.
- Gemini CLI: `timeout`, milliseconds, already 600000 by default.
- Cline: no key in the file — a setting in the MCP servers panel.

If it fails another way, the message names the variable at fault. The usual
causes, in order:

1. **Odoo Online without `ODOO_DB`** — go back to step 3.
2. **The key was mistyped or has expired** — ask them to make a new one
   (step 2) and replace it.
3. **The app cannot find `uvx`** — use the absolute path from step 6.

## 8. Tell the person what you did

Close in their language, plainly, with:

1. **Which file you wrote**, by full path (skip for the hosted route).
2. **What you set** — the Odoo address, the database if any, the access
   level; never repeat the key itself.
3. **That the key sits as plain text in that file.** Anyone who can read the
   file, or any backup or repository it reaches, has it. Say so explicitly if
   the file is inside a git repository. They can delete the key in Odoo at
   any time: *Preferences → Account Security → API Keys → Delete API key*.
4. **What you may do from now on** — only read, or read plus create and
   update; that deletion is never possible; that when the key's duration runs
   out they create a new one the same way and ask you to swap it in.
5. **Three things they can ask you right away**, for example *"which
   quotations are still open?"*, *"show me this month's invoices by
   customer"*, *"what activities do I have today?"*.
