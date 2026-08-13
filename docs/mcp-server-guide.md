# Building an MCP Server — 2026-07-28 Protocol Guide

> Comprehensive reference for building, packaging and publishing an MCP server
> with the latest specification. Adapted for wrapping Odoo XML-RPC scripts.
>
> **Sources:** [modelcontextprotocol.io](https://modelcontextprotocol.io)
> spec 2026-07-28, SDK docs, and registry documentation.

---

## Table of Contents

1. [What's New in 2026-07-28](#1-whats-new-in-2026-07-28)
2. [Protocol Architecture](#2-protocol-architecture)
3. [Building a Server with Python SDK 2.0.0+](#3-building-a-server-with-python-sdk-200)
4. [Transport: stdio vs HTTP](#4-transport-stdio-vs-http)
5. [Extensions (New)](#5-extensions-new)
6. [Publishing to the MCP Registry](#6-publishing-to-the-mcp-registry)
7. [Wrapping Odoo Scripts as MCP Tools](#7-wrapping-odoo-scripts-as-mcp-tools)
8. [Security for an Odoo MCP Server](#8-security-for-an-odoo-mcp-server)
9. [Quick Reference](#9-quick-reference)

---

## 1. What's New in 2026-07-28

Source: [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)

### Major changes from 2025-11-25

| Change | Impact |
|--------|--------|
| **Removed `Mcp-Session-Id` header** from Streamable HTTP | Stateless HTTP transport — no session management at protocol level |
| **`server/discover` replaces `initialize`** | Capability negotiation is now stateless and per-request |
| **Tool annotations are untrusted** | `description` and annotations from servers are advisory only — hosts MUST NOT trust them for security decisions |
| **Logging deprecated** (SEP-2577) | `notifications/message` logging removed; use stderr for stdio servers |
| **Elicitation added to core** | Servers can request structured input from users mid-operation |
| **Tasks extension** | Long-running async operations with polling and durable handles |
| **MCP Apps** | Inline UI elements (charts, forms, video) rendered in the conversation |
| **Skills over MCP** | Structured instructions discovered and consumed via MCP |

### What this means practically

- **No more sessions.** Each request is self-contained. The server doesn't
  need to track state between calls.
- **Discover instead of initialize.** Clients call `server/discover` to learn
  capabilities. This can be cached (TTL-based) but is fundamentally stateless.
- **Your tools are untrusted by default.** The host (Claude, Hermes, etc.)
  will ask the user for consent before calling any tool, regardless of what
  your tool description says.

---

## 2. Protocol Architecture

Source: [specification/basic](https://modelcontextprotocol.io/specification/2026-07-28/basic/index)

```
┌─────────┐     JSON-RPC 2.0     ┌─────────┐
│  Host   │ ◄──────────────────► │ Server  │
│ (Claude)│   stdio or HTTP      │ (Yours) │
└─────────┘                      └─────────┘
```

### Three roles

| Role | Description |
|------|-------------|
| **Host** | The LLM application (Claude Desktop, Hermes, etc.) |
| **Client** | Connector within the host that manages one server connection |
| **Server** | Your process — provides tools, resources, prompts |

### What servers can offer

| Feature | Model | Purpose |
|---------|-------|---------|
| **Tools** | `tool` | Functions the LLM can call (with user approval) |
| **Resources** | `resource` | Data the LLM can read (files, API responses) |
| **Prompts** | `prompt` | Pre-written templates for specific tasks |

### What clients can offer back

| Feature | Purpose |
|---------|---------|
| **Elicitation** | Server-initiated requests for structured input from the user |

---

## 3. Building a Server with Python SDK 2.0.0+

Source: [build-server tutorial](https://modelcontextprotocol.io/docs/2026-07-28/develop/build-server)

### Requirements

- Python 3.10+
- `mcp` SDK version 2.0.0 or higher

### Minimal server

```python
from mcp.server import MCPServer

mcp = MCPServer("my-server")

@mcp.tool()
async def hello(name: str) -> str:
    """Say hello to someone.

    Args:
        name: The person's name
    """
    return f"Hello, {name}!"

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

### Key SDK patterns

**Type hints become tool schemas automatically.** The docstring becomes the
tool description. Args section maps to parameter descriptions.

```python
@mcp.tool()
async def search_records(
    model: str,
    domain: list,
    fields: list,
    limit: int = 80
) -> str:
    """Search records in Odoo.

    Args:
        model: The Odoo model name (e.g. 'sale.order')
        domain: Search domain as list of tuples
        fields: Field names to return
        limit: Maximum records (default 80)
    """
    # ... implementation ...
    return str(results)
```

### Logging rules (critical for stdio)

> **NEVER use `print()` in a stdio server.** It corrupts the JSON-RPC stream.

```python
import logging

logger = logging.getLogger(__name__)

# ❌ BAD — breaks the protocol
print("Processing request")

# ✅ GOOD — goes to stderr, host captures it
logger.info("Processing request")
```

For HTTP transport, stdout is fine — it doesn't interfere with HTTP responses.

---

## 4. Transport: stdio vs HTTP

Source: [specification/architecture/transports](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)

### stdio (default, recommended for local tools)

```
Host spawns your process → communicates via stdin/stdout JSON-RPC
```

- Simplest to set up
- No port management
- The host manages the lifecycle
- **Must not write to stdout** (only JSON-RPC messages)

Config in Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "odoo": {
      "command": "uv",
      "args": ["--directory", "/ABSOLUTE/PATH/TO/odoo-mcp", "run", "server.py"]
    }
  }
}
```

### Streamable HTTP (for remote/shared servers)

- Stateless in 2026-07-28 (no `Mcp-Session-Id`)
- Suitable for deployment behind a reverse proxy
- Each request is independent

```python
mcp.run(transport="http", host="0.0.0.0", port=8080)
```

### When to use which

| | stdio | HTTP |
|---|---|---|
| Local tools | ✅ | ❌ |
| Shared/team access | ❌ | ✅ |
| No port management | ✅ | ❌ |
| Host manages lifecycle | ✅ | ❌ |
| Credentials in env | ✅ | via reverse proxy |

**For an Odoo MCP server: use stdio.** The server reads credentials from
environment variables and the host manages the process.

---

## 5. Extensions (New)

Source: [extensions/overview](https://modelcontextprotocol.io/extensions/overview)

Extensions are optional capabilities negotiated per-request via `_meta` fields.
Format: `{vendor-prefix}/{extension-name}`.

Official extensions use `io.modelcontextprotocol/` prefix.

### 5.1 Tasks (async long-running operations)

Source: [extensions/tasks/overview](https://modelcontextprotocol.io/extensions/tasks/overview)

```python
# A task starts, returns a handle, and the client polls
{
  "method": "tasks/start",
  "params": { "tool": "generate_report", "arguments": {...} }
}
# → { "taskHandle": "task_abc123", "status": "running" }

# Client polls
{ "method": "tasks/get", "params": { "handle": "task_abc123" } }
# → { "status": "completed", "result": {...} }
```

**Use case for Odoo:** generating large PDF reports, bulk operations,
census runs that take 30+ seconds.

### 5.2 Elicitation (server asks the user)

Source: [specification/client/features](https://modelcontextprotocol.io/specification/2026-07-28/client)

The server can request structured input from the user mid-operation:

```python
# Server requests confirmation before a destructive operation
result = await ctx.elicit({
    "message": "This will confirm 5 sales orders. Continue?",
    "schema": {
        "type": "object",
        "properties": {
            "confirm": {"type": "boolean"}
        },
        "required": ["confirm"]
    }
})
if not result["confirm"]:
    return "Operation cancelled by user."
```

**Use case for Odoo:** the safety layer (L3+ operations) can use elicitation
to get explicit user consent instead of relying on the host's generic approval.

### 5.3 MCP Apps (inline UI)

Source: [extensions/apps/overview](https://modelcontextprotocol.io/extensions/apps/overview)

Servers can return HTML that renders inline in the conversation:

```python
@mcp.tool()
async def show_dashboard() -> dict:
    """Show the Odoo accounting dashboard."""
    return {
        "content": [{
            "type": "text",
            "text": "<html>...chart...</html>",
            "_meta": {"mimeType": "text/html;profile=mcp-app"}
        }]
    }
```

### 5.4 Skills over MCP

Source: [community/working-groups/skills-over-mcp](https://modelcontextprotocol.io/community/working-groups/skills-over-mcp)

Servers can expose structured instructions (like agent skills) discoverable
via MCP. This is experimental.

---

## 6. Publishing to the MCP Registry

Source: [registry/about](https://modelcontextprotocol.io/registry/about),
[registry/authentication](https://modelcontextprotocol.io/registry/authentication)

### What the Registry is

- **Centralized metadata** for publicly accessible MCP servers
- Backed by Anthropic, GitHub, PulseMCP, Microsoft
- Hosts **metadata** (not code) — code stays on npm/PyPI/Docker Hub
- REST API for discovery by clients and aggregators

### server.json format

Source: [server.schema.json](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/server-json/draft/server.schema.json)

```json
{
  "$schema": "https://raw.githubusercontent.com/modelcontextprotocol/registry/main/docs/reference/server-json/draft/server.schema.json",
  "name": "io.github.crottolo/odoo-mcp",
  "description": "Operate an Odoo 18 instance via MCP: query, write, workflows",
  "version": "1.0.0",
  "packages": [
    {
      "registryType": "pypi",
      "identifier": "odoo-mcp",
      "version": "1.0.0"
    }
  ],
  "remotes": [],
  "installations": [
    {
      "type": "stdio",
      "command": "uvx",
      "args": ["odoo-mcp"],
      "env": {
        "ODOO_BASE_URL": "${ODOO_BASE_URL}",
        "ODOO_DB": "${ODOO_DB}",
        "ODOO_USER": "${ODOO_USER}",
        "ODOO_API_KEY": "${ODOO_API_KEY}"
      }
    }
  ]
}
```

### Namespace authentication

Namespaces use **reverse DNS** format tied to verified accounts:

| Namespace | How to verify |
|-----------|---------------|
| `io.github.username/*` | GitHub OAuth (automatic) |
| `com.example/*` | DNS TXT record on `example.com` |
| `com.example/*` | HTTP challenge on `example.com` |

The registry verifies that you own the namespace before accepting a
`server.json` submission.

### Package types supported

Source: [registry/package-types](https://modelcontextprotocol.io/registry/package-types)

| Type | Example |
|------|---------|
| **npm** | `npm:package-name` |
| **PyPI** | `pypi:package-name` (via `uvx` or `pipx`) |
| **Docker** | `docker:image:tag` |
| **Remote** | `https://server.example.com/mcp` |

### Publishing workflow

1. **Build** your server as a package (PyPI for Python)
2. **Publish** the package to PyPI/Docker Hub
3. **Create** `server.json` with correct metadata
4. **Authenticate** via GitHub OAuth or DNS verification
5. **Submit** `server.json` to the registry REST API
6. **Aggregators** (Claude store, PulseMCP) pull from the registry

### Registry REST API

```
POST https://registry.modelcontextprotocol.io/v0/servers
Authorization: Bearer <github-token>
Content-Type: application/json

{ "server.json content..." }
```

> The registry is in **preview** — breaking changes may occur before GA.

---

## 7. Wrapping Odoo Scripts as MCP Tools

This is the practical adaptation guide for the existing Odoo skill at
`~/.agents/skills/odoo/` (9 Python scripts, XML-RPC, stdlib only).

### Architecture

```
┌───────────────────┐
│  Claude / Hermes   │
└────────┬──────────┘
         │ MCP (stdio, JSON-RPC)
┌────────▼──────────┐
│  odoo_mcp.py       │  ← new thin MCP wrapper
│  (mcp SDK 2.0+)    │
└────────┬──────────┘
         │ imports
┌────────▼──────────┐
│  odoo/scripts/     │  ← existing code (unchanged)
│  odoo_client.py    │
│  safety_layer.py   │
│  write_patterns.py │
│  documents.py      │
│  collaboration.py  │
│  census.py         │
│  query.py          │
│  view_first.py     │
│  explore_module.py │
└────────┬──────────┘
         │ XML-RPC
┌────────▼──────────┐
│  Odoo 18 instance  │
└───────────────────┘
```

### Complete server skeleton

```python
#!/usr/bin/env python3
"""Odoo MCP Server — exposes Odoo operations as MCP tools.

Requires: mcp SDK >= 2.0.0, Python 3.10+
Run: uv run odoo_mcp.py  (or python3 odoo_mcp.py)
"""

import logging
import os
import sys

# --- Import the existing skill scripts (no changes to them) ---
SKILL_DIR = os.path.expanduser("~/.agents/skills/odoo/scripts")
sys.path.insert(0, SKILL_DIR)

from mcp.server import MCPServer

from odoo_client import connect, OdooError
from safety_layer import classify
from write_patterns import Writer
from documents import Documents
from collaboration import Collab
from census import run_census

logger = logging.getLogger(__name__)
mcp = MCPServer("odoo-mcp")

# --- Connection management ---
_odoo = None

def _get_odoo():
    """Lazily connect (credentials from environment only)."""
    global _odoo
    if _odoo is None:
        _odoo = connect()  # reads ODOO_BASE_URL, ODOO_DB, ODOO_USER, ODOO_API_KEY
    return _odoo


# ====================================================================
# READ TOOLS (L0 — safe, no consent beyond default)
# ====================================================================

@mcp.tool()
async def search_read(
    model: str,
    domain: list,
    fields: list,
    limit: int = 80
) -> str:
    """Search and read records from Odoo.

    Args:
        model: Odoo model name (e.g. 'sale.order', 'account.move')
        domain: Search domain, e.g. [["state", "=", "sale"]]
        fields: Fields to return, e.g. ["name", "amount_total_signed", "state"]
        limit: Maximum number of records (default 80)
    """
    o = _get_odoo()
    try:
        # Pass company context for multi-company instances
        ctx = {"allowed_company_ids": [2, 1]}  # adjust per instance
        results = o.search_read(model, domain, fields=fields, limit=limit, context=ctx)
        return _format_results(results)
    except OdooError as e:
        return f"Error: {e}"


@mcp.tool()
async def read_record(
    model: str,
    record_id: int,
    fields: list
) -> str:
    """Read specific fields of a single record.

    Args:
        model: Odoo model name
        record_id: The record ID
        fields: Fields to read — ALWAYS name them explicitly
    """
    o = _get_odoo()
    results = o.read(model, [record_id], fields=fields)
    return _format_results(results)


@mcp.tool()
async def count_records(
    model: str,
    domain: list
) -> str:
    """Count records matching a domain.

    Args:
        model: Odoo model name
        domain: Search domain
    """
    o = _get_odoo()
    n = o.search_count(model, domain)
    return f"{model}: {n} records match {domain}"


# ====================================================================
# CENSUS / PROFILE TOOLS
# ====================================================================

@mcp.tool()
async def instance_overview() -> str:
    """Get a summary of the Odoo instance: modules, volumes, companies."""
    o = _get_odoo()
    census = run_census(o)
    return census.summary()


@mcp.tool()
async def explore_module(module_name: str) -> str:
    """Generate a reference document for a module by querying the instance.

    Args:
        module_name: Technical name (e.g. 'sale', 'account', 'helpdesk')
    """
    # Runs explore_module.py logic and returns the reference
    import subprocess
    result = subprocess.run(
        ["python3", f"{SKILL_DIR}/explore_module.py", module_name],
        capture_output=True, text=True, timeout=60
    )
    return result.stdout[:5000]  # cap output size


# ====================================================================
# WRITE TOOLS (L1-L5 — safety layer gates every call)
# ====================================================================

@mcp.tool()
async def write_record(
    model: str,
    record_id: int,
    values: dict
) -> str:
    """Write values to a record. Safety layer classifies the operation first.

    Args:
        model: Odoo model name
        record_id: The record to modify
        values: Field-value pairs to write
    """
    o = _get_odoo()

    # Safety layer classifies BEFORE executing
    level = classify(model, "write", {"ids": [record_id], "vals": values})
    if level and "L4" in str(level) or "L5" in str(level):
        return f"BLOCKED by safety layer: {level}. This operation is destructive."

    try:
        before = o.read(model, [record_id], fields=list(values.keys()))
        o.write(model, [record_id], values)
        after = o.read(model, [record_id], fields=list(values.keys()))
        return f"Written. Before: {before}\nAfter: {after}"
    except OdooError as e:
        # Pattern 2: an exception does NOT mean nothing happened
        after = o.read(model, [record_id], fields=list(values.keys()))
        return f"Exception raised BUT the record may have changed.\nError: {e}\nCurrent state: {after}"


@mcp.tool()
async def run_action(
    model: str,
    method: str,
    record_ids: list
) -> str:
    """Run a workflow action (e.g. action_confirm, action_post).

    The safety layer classifies every action. L4+ operations are blocked.

    Args:
        model: Odoo model name
        method: The action method name (e.g. 'action_confirm')
        record_ids: List of record IDs
    """
    o = _get_odoo()

    level = classify(model, method, {"ids": record_ids})
    if level and ("L4" in str(level) or "L5" in str(level)):
        return f"BLOCKED: {level}"

    w = Writer(o)
    try:
        result = w.act(model, method, record_ids, watch="state")
        return f"Done. {result.before} → {result.after}"
    except Exception as e:
        # Pattern 2: may have executed despite the exception
        state = o.read(model, record_ids, fields=["state"])
        return f"Exception: {e}\nCurrent state: {state}\nDo NOT retry blindly."


# ====================================================================
# DOCUMENT TOOLS
# ====================================================================

@mcp.tool()
async def download_attachment(
    model: str,
    record_id: int,
    dest_dir: str = "/tmp"
) -> str:
    """Download all attachments (chatter files, PDFs) from a record.

    Args:
        model: Odoo model name
        record_id: Record ID
        dest_dir: Where to save files (default /tmp)
    """
    o = _get_odoo()
    d = Documents(o)
    result = d.download(model, record_id, dest_dir=dest_dir)
    saved = result.get("saved", [])
    skipped = result.get("skipped", [])
    msg = f"Saved {len(saved)} files"
    if skipped:
        msg += f", skipped {len(skipped)} (filestore missing on disk)"
    return msg


@mcp.tool()
async def generate_pdf(
    model: str,
    record_id: int,
    dest_dir: str = "/tmp"
) -> str:
    """Generate and download the PDF for a record (invoice, order, etc.).

    Args:
        model: Odoo model name (e.g. 'account.move')
        record_id: Record ID
        dest_dir: Where to save the PDF
    """
    o = _get_odoo()
    d = Documents(o)
    path = d.generate_pdf(model, record_id, dest_dir=dest_dir)
    return f"PDF saved: {path}"


# ====================================================================
# COLLABORATION TOOLS
# ====================================================================

@mcp.tool()
async def notify_user(
    model: str,
    record_id: int,
    message: str,
    user_ids: list
) -> str:
    """Notify internal users about a record (inbox notification, not email).

    Uses message_notify — does NOT add followers, does NOT email customers.

    Args:
        model: Odoo model name
        record_id: Record ID
        message: The message body (plain text)
        user_ids: User IDs to notify
    """
    o = _get_odoo()
    d = Documents(o)
    result = d.tell(model, record_id, message, users=user_ids)
    return str(result)


@mcp.tool()
async def create_activity(
    model: str,
    record_id: int,
    summary: str,
    user_id: int,
    days_deadline: int = 7
) -> str:
    """Create a planned activity on a record.

    Args:
        model: Odoo model name
        record_id: Record to link the activity to
        summary: Short description
        user_id: User to assign
        days_deadline: Days until due (default 7)
    """
    o = _get_odoo()
    c = Collab(o)
    result = c.todo(model, record_id, summary, user_id=user_id, days=days_deadline)
    return f"Activity {result['id']} created for user {user_id}, due in {days_deadline} days"


# ====================================================================
# HELPERS
# ====================================================================

def _format_results(records):
    """Format Odoo records as readable text for the LLM."""
    if not records:
        return "No records found."
    lines = []
    for r in records:
        parts = []
        for k, v in r.items():
            if k == "id":
                continue
            parts.append(f"{k}={v}")
        lines.append(f"[{r.get('id', '?')}] " + ", ".join(parts))
    return "\n".join(lines)


# ====================================================================
# MAIN
# ====================================================================

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

### Pyproject.toml for packaging

```toml
[project]
name = "odoo-mcp"
version = "1.0.0"
description = "Operate an Odoo 18 instance via MCP"
requires-python = ">=3.10"
dependencies = ["mcp[cli]>=2.0.0"]

[project.scripts]
odoo-mcp = "odoo_mcp:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### Claude Desktop config

```json
{
  "mcpServers": {
    "odoo": {
      "command": "uvx",
      "args": ["odoo-mcp"],
      "env": {
        "ODOO_BASE_URL": "http://dev8069:8069",
        "ODOO_DB": "persevida_dev18",
        "ODOO_USER": "admin",
        "ODOO_API_KEY": "your-key-here"
      }
    }
  }
}
```

### Hermes config

```bash
hermes mcp add odoo \
  --env ODOO_BASE_URL=http://dev8069:8069 \
         ODOO_DB=persevida_dev18 \
         ODOO_USER=admin \
         ODOO_API_KEY=your-key \
  --args run odoo_mcp.py
```

---

## 8. Security for an Odoo MCP Server

### The trust model in MCP 2026-07-28

Source: [specification/security](https://modelcontextprotocol.io/specification/2026-07-28)

> **Tool annotations are UNTRUSTED.** The host treats all tool descriptions
> as advisory. The user must explicitly consent to every tool call.

This means: **your safety layer is the real gate, not the host's consent dialog.**

### Mapping the L0–L5 safety layer to MCP

| Level | Meaning | MCP behavior |
|-------|---------|--------------|
| L0 | Read-only | Normal tool call |
| L1 | Benign write (create, note, activity) | Normal tool call |
| L2 | Moderate write (write fields) | Host asks for consent |
| L3 | State transition (confirm, post, validate) | Host asks for consent |
| L4 | Destructive (cancel, unlink) | **Tool refuses** — return error |
| L5 | Unknown method | **Tool refuses** — return error |

The MCP server enforces this internally. The host's consent dialog is a
**second** layer of protection, not the first.

### Credential handling

```
✅ Environment variables (ODOO_BASE_URL, ODOO_DB, ODOO_USER, ODOO_API_KEY)
✅ ~/.hermes/secrets/odoo-dev.env (chmod 600)
❌ Hardcoded in server.json (published to registry)
❌ Hardcoded in source code
❌ In tool descriptions or docstrings
```

For publishing to the MCP Registry, the `server.json` uses **placeholders**:
```json
"env": {
    "ODOO_API_KEY": "${ODOO_API_KEY}"
}
```

The host substitutes environment variables at launch time.

### Elicitation for L3+ operations

With the 2026-07-28 spec, you can use elicitation to get **structured
consent** from the user before a state transition:

```python
@mcp.tool()
async def confirm_order(order_id: int) -> str:
    """Confirm a sales order."""
    level = classify("sale.order", "action_confirm", {"ids": [order_id]})
    if "L3" in str(level):
        # Ask the user directly via elicitation
        result = await ctx.elicit({
            "message": f"Confirm sales order {order_id}? This will trigger delivery and invoicing.",
            "schema": {"type": "object", "properties": {"confirm": {"type": "boolean"}}}
        })
        if not result.get("confirm"):
            return "Cancelled by user."
    # ... proceed ...
```

---

## 9. Quick Reference

### MCP SDK 2.0.0+ cheat sheet

```python
from mcp.server import MCPServer

mcp = MCPServer("my-server")

@mcp.tool()
async def my_tool(param: str) -> str:
    """Tool description (becomes the MCP tool description)."""
    ...

if __name__ == "__main__":
    mcp.run(transport="stdio")      # local
    # mcp.run(transport="http", port=8080)  # remote
```

### Key rules

1. **Never `print()` in stdio** — use `logging` (goes to stderr)
2. **Type hints = tool schema** — annotate everything
3. **Docstrings = descriptions** — the LLM reads them
4. **Each request is stateless** — don't rely on server-side session state
5. **Tool annotations are untrusted** — the host doesn't believe them
6. **Elicitation for consent** — ask the user mid-operation when needed

### Useful links

| Resource | URL |
|----------|-----|
| Build server tutorial | https://modelcontextprotocol.io/docs/2026-07-28/develop/build-server |
| Full specification | https://modelcontextprotocol.io/specification/2026-07-28 |
| Extensions overview | https://modelcontextprotocol.io/extensions/overview |
| Tasks extension | https://modelcontextprotocol.io/extensions/tasks/overview |
| MCP Apps | https://modelcontextprotocol.io/extensions/apps/overview |
| Registry about | https://modelcontextprotocol.io/registry/about |
| Registry auth | https://modelcontextprotocol.io/registry/authentication |
| server.json schema | https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/server-json/draft/server.schema.json |
| Changelog (2026-07-28) | https://modelcontextprotocol.io/specification/2026-07-28/changelog |
| Debugging guide | https://modelcontextprotocol.io/docs/2026-07-28/tools/debugging |

---

*Document generated 2026-08-13. Based on MCP specification version 2026-07-28.
All code examples are illustrative — test against your instance before production use.*
