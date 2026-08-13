# PRD: Odoo Assistant

> **Product Requirements Document** — A self-evolving Odoo virtual employee
> MCP server, compatible with the 2026-07-28 specification.
>
> Author: Roberto Crotti / ZCode
> Date: 2026-08-13 (revised)
> Status: Draft for review
> Spec version: MCP 2026-07-28 · Python SDK ≥ 2.0.0
> Odoo: 14+ (XML-RPC universal), best on 18

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Goals and Non-Goals](#2-goals-and-non-goals)
3. [Market Analysis: Who Consumes MCP](#3-market-analysis-who-consumes-mcp)
4. [Architecture](#4-architecture)
5. [Tool Inventory](#5-tool-inventory)
6. [Authentication Strategy](#6-authentication-strategy)
7. [Safety Layer Integration](#7-safety-layer-integration)
8. [Transport Strategy](#8-transport-strategy)
9. [Repository Structure](#9-repository-structure)
10. [Build and Packaging](#10-build-and-packaging)
11. [Publishing Pipeline](#11-publishing-pipeline)
12. [Testing Strategy](#12-testing-strategy)
13. [Documentation Plan](#13-documentation-plan)
14. [Operational Runbook](#14-operational-runbook)
15. [Milestone Plan](#15-milestone-plan)
16. [Risk Register](#16-risk-register)
17. [Open Questions](#17-open-questions)

---

## 1. Executive Summary

The Odoo skill (`~/.agents/skills/odoo/`, v1.2.0) currently operates as a
file-based skill for Hermes and Claude Code. It is **not a collection of
scripts** — it is a self-evolving Odoo virtual employee with four pillars:

1. **View-first navigation**: menus and actions resolved from the real Odoo
   UI tree, not guessed from model names. A menu → view → field path is
   discovered, not assumed.
2. **Auto-discovery**: `explore_module.py` interrogates the instance and
   generates reference documents with real field names, state distributions,
   and volumes — after every Odoo upgrade, the references regenerate
   themselves.
3. **Auto-evolution**: the census layer detects what modules are *used*
   (not just installed), surfaces anomalies (expired subscriptions, orphan
   records), and feeds the instance profile that every query reads from.
4. **Safety layer**: L0–L5 classification of every operation, learned from
   12 cold-start runs where the most dangerous bugs were invisible without
   executing against a live instance.

It contains 9 Python scripts that use XML-RPC to talk to Odoo (14+),
12 verified write patterns, and 23 reference documents — all generated
by interrogation, never written from documentation.

**The goal is to expose this assistant as an MCP server** — publishable to
the MCP Registry, installable by anyone via `uvx odoo-assistant`, and usable from
Claude Desktop, ChatGPT, Cursor, VS Code Copilot, Goose, and any other
MCP-compatible host.

This is **not a rewrite**. The existing scripts remain the source of truth.
The MCP server is a thin layer that exposes their capabilities as MCP tools
and resources, passing through the safety layer on every call.

### Key constraint

The skill's 7 non-negotiable rules (never `amount_total`, always company
context, re-read after write, etc.) are enforced by the safety layer inside
the tools, not by the host's consent dialog. This is critical because the
MCP spec explicitly states that **tool annotations are untrusted** — the host
will not enforce your rules for you.

---

## 2. Goals and Non-Goals

### Goals

| # | Goal | Success metric |
|---|------|----------------|
| G1 | Anyone can install via `uvx odoo-assistant` or pip | PyPI package, `pip install odoo-assistant` works |
| G2 | Discoverable in the MCP Registry | Published `server.json`, searchable at `registry.modelcontextprotocol.io` |
| G3 | Works with all major MCP hosts | Tested on Claude Desktop, ChatGPT, Cursor, VS Code Copilot, Hermes |
| G4 | Zero credentials in source or registry | Env vars only, `server.json` uses `${VAR}` placeholders |
| G5 | Safety layer enforces L0–L5 on every call | Unit tests for every L-level path |
| G6 | The SKILL.md and references are consumable as MCP resources | Resources expose `odoo://skill`, `odoo://ref/*` |
| G7 | Published as PyPI package (not npm/Docker) | One command: `uvx odoo-assistant` |
| G8 | CI/CD pipeline for automated publish | GitHub Actions: tag → PyPI → MCP Registry |

### Non-Goals

| # | Non-goal | Why |
|---|----------|-----|
| N1 | Remote HTTP server (Streamable HTTP) | Odoo credentials are per-user; stdio keeps them local. Remote can be a future milestone. |
| N2 | Rewrite the Odoo scripts | They work, they're tested. The MCP layer wraps them. |
| N3 | MCP Apps (inline UI) | Nice-to-have future milestone, not needed for v1. |
| N4 | Skills over MCP (`skill://` scheme) | SEP-2640 is still draft. Resources work today; upgrade later. |
| N5 | Support Odoo ≤ 13 | XML-RPC exists since Odoo 8, but API Keys require 14+. Password auth is a security regression we won't ship. |
| N6 | Multi-tenant SaaS | Each user connects to their own Odoo instance. |

---

## 3. Market Analysis: Who Consumes MCP

Source: [Extension Support Matrix](https://modelcontextprotocol.io/extensions/client-matrix)

### Hosts that support MCP (core tools + resources)

| Host | stdio | HTTP | MCP Apps | Notes |
|------|:-----:|:----:|:--------:|-------|
| **Claude Desktop** | ✅ | ✅ | ✅ | Primary target, Custom Connectors |
| **ChatGPT (OpenAI)** | ✅ | ✅ | ✅ | Supports MCP since 2025; uses Custom Connectors |
| **Cursor** | ✅ | ✅ | ✅ | IDE with MCP support |
| **VS Code Copilot** | ✅ | ✅ | ✅ | GitHub Copilot MCP integration |
| **Microsoft 365 Copilot** | ✅ | ✅ | ✅ | Enterprise MCP support |
| **Goose (Block)** | ✅ | ✅ | ✅ | Open-source agent |
| **Postman** | ✅ | ✅ | ✅ | API testing with MCP |
| **Hermes** | ✅ | — | — | Our own agent, `hermes mcp add` |
| **Claude Code** | ✅ | ✅ | — | CLI agent |
| **MCPJam** | ✅ | ✅ | ✅ | MCP marketplace |

### Key insight

**OpenAI/ChatGPT supports MCP.** This is not a Claude-only ecosystem. A
published MCP server is usable by ChatGPT users too, via the Custom Connectors
or Custom GPTs integration.

Source: [Connect to remote MCP servers](https://modelcontextprotocol.io/docs/2026-07-28/develop/connect-remote-servers)

---

## 4. Architecture

```
┌──────────────────────────────────────────────────────┐
│  MCP Host (Claude / ChatGPT / Cursor / Hermes / ...)  │
│                                                       │
│  1. Discovers tools via tools/list (server/discover)  │
│  2. User approves tool call                           │
│  3. Calls tool → receives JSON result                 │
│  4. Can read resources (SKILL.md, references)         │
└───────────────────────┬──────────────────────────────┘
                        │ JSON-RPC 2.0 over stdio
                        │ (host spawns process)
┌───────────────────────▼──────────────────────────────┐
│  odoo-assistant (our server)                                │
│  ┌───────────────────────────────────────────────┐   │
│  │  MCPServer("odoo-assistant")                      │   │
│  │                                               │   │
│  │  Tools (12)          Resources (24)           │   │
│  │  search_read         odoo://skill (SKILL.md) │   │
│  │  read_record         odoo://ref/payments      │   │
│  │  count_records       odoo://ref/writing       │   │
│  │  write_record        odoo://ref/deletion      │   │
│  │  run_action          odoo://ref/documents     │   │
│  │  create_record       odoo://ref/collaboration │   │
│  │  download_docs       odoo://ref/recipes       │   │
│  │  generate_pdf        ...                       │   │
│  │  notify_user                                   │   │
│  │  create_activity                                │   │
│  │  instance_overview                              │   │
│  │  explore_module                                 │   │
│  │  cancel_record                                  │   │
│  └─────────────────────┬─────────────────────────┘   │
│                        │ imports (sys.path)            │
│  ┌─────────────────────▼─────────────────────────┐   │
│  │  Odoo scripts (bundled in package)             │   │
│  │  odoo_client.py · safety_layer.py · ...        │   │
│  │  (UNCHANGED from skill v1.2.0)                 │   │
│  └─────────────────────┬─────────────────────────┘   │
│                        │ XML-RPC                       │
│  ┌─────────────────────▼─────────────────────────┐   │
│  │  Odoo 18 instance (user's own)                 │   │
│  └───────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────┘
```

*Note: While `server/discover` exists in the MCP specification, the actual tool listing in practice goes through `tools/list` (not `server/discover` alone). The `initialize` request remains a legacy fallback per the spec.*

### Design principle: thin wrapper

The MCP server is **~200 lines** of tool definitions. All business logic
stays in the existing scripts. This means:

- Bug fixes to the scripts propagate to the MCP server automatically
- The skill (for Hermes/Claude Code) and the MCP server share the same code
- Tests for the scripts are tests for the MCP server

---

## 5. Tool Inventory

Each tool maps to an existing script function. The safety layer classifies
every call.

### Read tools (L0 — safe)

| Tool | Input | Output | Maps to |
|------|-------|--------|---------|
| `search_read` | model, domain, fields, limit | JSON array | `Odoo.search_read` (shortcut) |
| `read_record` | model, id, fields | JSON object | `Odoo.call(model, "read", ...)` |
| `count_records` | model, domain | integer | `Odoo.search_count` (shortcut) |
| `instance_overview` | none | text summary | `query.py` |
| `explore_module` | module_name | reference text | `explore_module.py` |

### Write tools (L1–L5 — safety layer gates)

*Note: Safety levels are computed dynamically at call time via `classify()` through `src/odoo_assistant/server_safety.py`'s `gate()` function. For example, a single-record write is L1 (not L2), and `run_action`'s level depends on the method string.*

| Tool | Input | Safety | Maps to |
|------|-------|--------|---------|
| `create_record` | model, values, unique_on | L1 | `Writer.create` |
| `write_record` | model, id, values | L1 (single-record) / L2 (batch) | `Writer.write` |
| `run_action` | model, method, ids | Dynamic (L1-L4) | `Writer.act` |
| `cancel_record` | model, id | L4 (destructive — blocked by default) | `Writer.act` with `action_cancel` |

### Collaboration tools (L1 — safe)

| Tool | Input | Output | Maps to |
|------|-------|--------|---------|
| `notify_user` | model, id, message, user_ids | delivery report | `Documents.tell` |
| `create_activity` | model, id, summary, user_id, deadline | activity id | `Collab.todo` |

### Document tools (L0–L2)

| Tool | Input | Output | Maps to |
|------|-------|--------|---------|
| `download_docs` | model, id, dest_dir | file paths + skipped report | `Documents.download` |
| `generate_pdf` | model, id, dest_dir | file path | `Documents.generate_pdf` |

### Tool naming conventions

Per spec [§tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools):
- 1–128 chars, `[A-Za-z0-9_.-]` only
- Unique within server
- Our convention: `snake_case` (e.g. `search_read`, `generate_pdf`)

---

## 5B. Complete Tool Specifications

Every tool's full signature, type contract, and output contract.

### Read tools

#### `search_read`
```python
@mcp.tool()
async def search_read(
    model: str,                          # "sale.order"
    domain: list,                        # [["state", "=", "sale"]]
    fields: list[str] | None = None,     # ["name", "amount_total"]
    limit: int = 80,                     # hard cap at 200
    offset: int = 0
) -> str:                                # JSON array, max 5000 chars
    """Search and read records in one call. Equivalent to Odoo search_read."""
```
**Error handling:** raises `ToolExecutionError` (resulting in `isError:true` on the wire) if the model is not found or the domain has syntax errors, handled via `src/odoo_assistant/server_errors.py`.
**Truncation:** if output > 5000 chars, return first N records + `"... truncated, use limit"`.
**Safety:** L0 (read-only).

#### `read_record`
```python
@mcp.tool()
async def read_record(
    model: str,
    record_id: int,
    fields: list[str] | None = None      # default: name + state fields
) -> str:
    """Read a single record by ID. Fields are always named (pattern 12)."""
```
**Safety:** L0.

#### `count_records`
```python
@mcp.tool()
async def count_records(
    model: str,
    domain: list | None = None
) -> str:
    """Count records matching a domain. Returns integer as string."""
```
**Safety:** L0.

#### `instance_overview`
```python
@mcp.tool()
async def instance_overview() -> str:
    """Get a summary of the connected Odoo instance: version, modules,
    volumes by area, anomalies. Equivalent to `query.py` with no args."""
```
**Safety:** L0. No input.

#### `explore_module`
```python
@mcp.tool()
async def explore_module(
    module_name: str,                    # "helpdesk", "sale"
    action: str = "generate"             # "list" | "generate"
) -> str:
    """Discover module structure from the live instance. Generates or
    retrieves a reference document with real field names, state values,
    and volumes. Auto-evolving: regenerates after Odoo upgrades."""
```
**Safety:** L0 (read-only discovery).

### Write tools

#### `create_record`
```python
@mcp.tool()
async def create_record(
    model: str,
    values: dict,                        # {"name": "Test", "partner_id": 1}
    unique_on: list[str] | None = None   # ["name"] for idempotency
) -> str:
    """Create a record. If unique_on is set, reuse an existing match
    (pattern 8 — no idempotency keys in Odoo)."""
```
**Safety:** L1. **Anti-retry:** returns existing record on duplicate call.

#### `write_record`
```python
@mcp.tool()
async def write_record(
    model: str,
    record_id: int,
    values: dict
) -> str:
    """Write field values to a record. Verifies before/after (pattern 9)."""
```
**Safety:** L1 (single-record) / L2 (batch). **Returns:** `"before: X → after: Y"` or `"NOT CHANGED"`.

#### `run_action`
```python
@mcp.tool()
async def run_action(
    model: str,
    method: str,                         # "action_confirm", "action_post"
    record_ids: list[int]
) -> str:
    """Run a workflow action. Follows wizards automatically (pattern 11).
    Handles OdooExecutedButUnserializable (pattern 2 — committed but raised)."""
```
**Safety:** Dynamic (L1-L4 depending on method). Blocked at L4+ by default.

#### `cancel_record`
```python
@mcp.tool()
async def cancel_record(
    model: str,
    record_id: int
) -> str:
    """Cancel a record via the proper wizard (action_cancel → sale.order.cancel).
    Pattern 11: action_cancel returns a dict, not a result — we follow it."""
```
**Safety:** L4 (destructive). **Requires** `ODOO_MCP_MAX_LEVEL >= 4`.

### Collaboration tools

#### `notify_user`
```python
@mcp.tool()
async def notify_user(
    model: str,
    record_id: int,
    message: str,                        # plain text or simple HTML
    user_ids: list[int],                 # who to notify
    subtype: str = "note"                # "note" (internal) | "comment" (emails followers!)
) -> str:
    """Notify users via chatter. DEFAULT is 'note' (internal only).
    WARNING: 'comment' sends email to ALL followers including external
    customers. The tool checks the audience first and warns if a customer
    is among followers."""
```
**Safety:** L1. **Anti-footgun:** refuses `comment` if external followers exist,
unless `force=True` is passed.

#### `create_activity`
```python
@mcp.tool()
async def create_activity(
    model: str,
    record_id: int,
    summary: str,
    user_id: int,
    days: int = 0,                       # deadline offset from today
    activity_type: str | None = None     # "email", "call", "meeting", "todo"
) -> str:
    """Schedule an activity (to-do) for a user on a record."""
```
**Safety:** L1.

### Document tools

#### `download_docs`
```python
@mcp.tool()
async def download_docs(
    model: str,
    record_id: int,
    dest_dir: str = "/tmp"               # where to save files
) -> str:
    """Download all attachments of a record (chatter files + binary fields).
    Returns JSON: {"saved": [...], "skipped": [...]}."""
```
**Safety:** L0. **Reports skipped files** with reason (missing filestore).

#### `generate_pdf`
```python
@mcp.tool()
async def generate_pdf(
    model: str,
    record_id: int,
    dest_dir: str = "/tmp"
) -> str:
    """Generate and download the PDF report for a record.
    Uses the print wizard, extracts the binary field (invoice_pdf_report_file),
    decodes base64, saves to disk."""
```
**Safety:** L3 (state-change/comms) because it can trigger the print/send wizard. **Requires** `ODOO_MCP_MAX_LEVEL >= 3`.

---

## 6. Authentication Strategy

### The problem

The Odoo MCP server needs 4 credentials to connect:
```
ODOO_BASE_URL  — e.g. http://localhost:8069
ODOO_DB        — e.g. persevida
ODOO_USER      — e.g. admin
ODOO_API_KEY   — the Odoo API key (NOT password)
```

These are **per-user** — every user has their own Odoo instance. They must
**never** be in the source code, the PyPI package, or the `server.json`.

### Solution: environment variables declared in server.json

The MCP Registry spec supports declaring environment variables with metadata:

Source: [registry/quickstart](https://modelcontextprotocol.io/registry/quickstart),
[package-types](https://modelcontextprotocol.io/registry/package-types)

```json
{
  "packages": [{
    "registryType": "pypi",
    "identifier": "odoo-assistant",
    "version": "1.0.0",
    "transport": { "type": "stdio" },
    "environmentVariables": [
      {
        "name": "ODOO_BASE_URL",
        "description": "Your Odoo instance URL (e.g. http://localhost:8069)",
        "isRequired": true,
        "isSecret": false,
        "format": "string"
      },
      {
        "name": "ODOO_DB",
        "description": "Odoo database name",
        "isRequired": true,
        "isSecret": false,
        "format": "string"
      },
      {
        "name": "ODOO_USER",
        "description": "Odoo login username",
        "isRequired": true,
        "isSecret": false,
        "format": "string"
      },
      {
        "name": "ODOO_API_KEY",
        "description": "Odoo API key (Settings > Users > API Keys)",
        "isRequired": true,
        "isSecret": true,
        "format": "string"
      }
    ]
  }]
}
```

When a user installs the server, the host (Claude, ChatGPT, etc.) shows a
form asking for these values. The user enters them once; they are stored in
the host's secret store (keychain, env file) and injected as environment
variables at launch.

### What the server code does

```python
import os

def _get_odoo():
    base = os.environ.get("ODOO_BASE_URL")
    db = os.environ.get("ODOO_DB")
    user = os.environ.get("ODOO_USER")
    key = os.environ.get("ODOO_API_KEY")

    if not all([base, db, user, key]):
        raise RuntimeError(
            "Missing Odoo credentials. Set ODOO_BASE_URL, ODOO_DB, "
            "ODOO_USER, ODOO_API_KEY environment variables."
        )

    return connect()  # existing function reads from env
```

### Who does NOT see the credentials

| Layer | Sees credentials? |
|-------|:-:|
| PyPI package | ❌ |
| `server.json` in registry | ❌ (placeholders only) |
| MCP Registry API | ❌ |
| Host application (Claude) | ✅ (stores in keychain) |
| Our server process | ✅ (reads from env) |
| Odoo instance | ✅ |

---

## 6B. Multi-Version Authentication

Source: [Odoo 19 external RPC API](https://www.odoo.com/documentation/19.0/developer/reference/external_rpc_api.html)

### Authentication timeline

| Odoo version | Auth methods | Notes |
|---|---|---|
| 8–13 | Password only | `authenticate(db, user, password, {})` |
| **14+** | **API Key** (new) + password | API Keys introduced in 14.0 |
| 16–18 | API Key recommended, password still works | Password deprecated but not removed |
| 19 | API Key recommended | JSON-2 API adds Bearer token auth |

### What the server accepts

```python
import os

def _get_credentials():
    """Resolve Odoo credentials with version-aware fallback.

    Odoo's authenticate() accepts BOTH passwords and API keys in the same
    parameter slot — it does not distinguish between them at the protocol
    level. We prefer API keys for security, but fall back to password for
    Odoo 13 and earlier where API keys don't exist.
    """
    base = os.environ.get("ODOO_BASE_URL")
    db   = os.environ.get("ODOO_DB")
    user = os.environ.get("ODOO_USER")
    key  = os.environ.get("ODOO_API_KEY")    # Odoo 14+ (preferred)
    pwd  = os.environ.get("ODOO_PASSWORD")    # Odoo ≤13 fallback

    secret = key or pwd
    if not secret:
        raise RuntimeError(
            "Set ODOO_API_KEY (Odoo 14+) or ODOO_PASSWORD (legacy). "
            "API keys: Settings > Users > API Keys > New."
        )
    return base, db, user, secret
```

### server.json environment variables

```json
"environmentVariables": [
  {"name": "ODOO_BASE_URL", "isRequired": true, "isSecret": false},
  {"name": "ODOO_DB", "isRequired": true, "isSecret": false},
  {"name": "ODOO_USER", "isRequired": true, "isSecret": false},
  {"name": "ODOO_API_KEY", "isRequired": false, "isSecret": true,
   "description": "Odoo 14+ API key (preferred). Settings > Users > API Keys."},
  {"name": "ODOO_PASSWORD", "isRequired": false, "isSecret": true,
   "description": "Legacy password for Odoo ≤13. NOT recommended."}
]
```

Both are `isRequired: false` because the user provides exactly one. The
server checks at startup and gives a clear error if neither is present.

---

## 7. Safety Layer Integration

Source: internal skill documentation, 12 cold-start runs.

### How the safety layer maps to MCP

The MCP spec says tool annotations are **untrusted** by hosts. This means the
host will ask the user for consent, but it won't enforce our business rules.
Our safety layer is the **enforcement point**.

```python
from odoo_assistant.server_safety import gate

@mcp.tool()
async def run_action(model: str, method: str, record_ids: list) -> str:
    """Run a workflow action on records."""
    # 1. Safety layer gates the call dynamically
    decision = gate(model, method, record_ids)

    # 2. Block if not allowed by the ceiling
    if not decision.allowed:
        return f"BLOCKED: {decision.reason}"

    # 3. Execute with Writer (verifies before/after)
    o = _get_odoo()
    w = Writer(o)
    result = w.act(model, method, record_ids, watch="state")
    return f"Done. {result.before} → {result.after}"
```

### L-level to MCP behavior mapping

| Level | Description | Server behavior | Host behavior |
|-------|-------------|-----------------|---------------|
| L0 | Read-only | Execute | Normal consent |
| L1 | Benign write (create, note) | Execute | Normal consent |
| L2 | Field write | Execute | Normal consent |
| L3 | State transition (confirm, post) | Execute | Normal consent |
| L4 | Destructive (cancel, unlink) | **Refuse** with message | N/A |
| L5 | Unknown method | **Refuse** with message | N/A |

### Configurable safety level

```python
# Users can raise the bar via env var
ODOO_MCP_MAX_LEVEL=2  # blocks L3 and above
```

This lets cautious users restrict the server to read-only (L0) or read+write
(L2) without touching the code.

*Note: The `ODOO_MCP_MAX_LEVEL` semantics, default value (3), and the ordinal mapping table are defined and enforced dynamically at call time in `src/odoo_assistant/server_safety.py`.*

---

## 7B. Error Handling — the Odoo Execution Trap

Source: internal skill testing, patterns 2 and 3 (12 cold-start runs).

### The trap

Odoo commits the database transaction **before** serializing the XML-RPC
response. When a method returns `None` (or a non-serializable recordset), the
server-side `xmlrpc.client.dumps()` raises `cannot marshal None unless
allow_none is enabled` — **after the commit**.

From the caller's perspective: the call raised an exception, so it "failed".
From the database's perspective: the operation succeeded and was committed.

**Retrying creates duplicates.** This was the single most dangerous bug in
the 12-run test campaign (run 10: invoice created despite exception, reported
as failed, then retried → double invoice).

### MCP mapping

The actual implemented model uses `isError:true` / `isError:false` via return-vs-raise, exactly as built in `src/odoo_assistant/server_errors.py`. The SDK converts raised exceptions (except `MCPError`) into `isError:true` with the exception's string representation as the message. Normal returns yield `isError:false`.

| Situation | MCP behavior | Wire Result |
|---|---|---|
| `OdooExecutedButUnserializable` | **Success with warning** — the operation committed, the result couldn't be serialized. Returns a warning string. | `isError:false` |
| Real Odoo error (AccessDenied, ValidationError) | **Error** — raises `ToolExecutionError` | `isError:true` |
| Network timeout / other exceptions | **Error** — raises `ToolExecutionError` with state unknown warning | `isError:true` |

### Tool implementation

```python
from odoo_assistant.server_errors import tool_result, handle_odoo_exception

@mcp.tool()
async def run_action(model: str, method: str, record_ids: list[int]) -> str:
    """Run a workflow action (confirm, post, validate, cancel...)."""
    try:
        o = _get_odoo()
        w = Writer(o)
        result = w.act(model, method, record_ids, watch="state")
        return tool_result(f"Done: {result.before} → {result.after}")
    except Exception as exc:
        return handle_odoo_exception(exc, lambda: w.state_of(model, record_ids)).deliver()
```

### Idempotency via `unique_on`

For `create_record`, the safety layer enforces `unique_on` (pattern 8):

```python
@mcp.tool()
async def create_record(model: str, values: dict, unique_on: list[str] | None = None) -> str:
    """Create a record. If unique_on is set, reuse an existing match."""
    w = Writer(_get_odoo())
    rid = w.create(model, values, unique_on=unique_on)
    return f"Created (or reused) {model} id={rid}"
```

Tested: three identical calls → one record (`tests/test_step_idempotence.py`).

---

## 7C. Version Detection and Adaptation

Source: internal skill (`payments.md` §"Odoo 17→18 field renames"),
[Odoo 19 external RPC API](https://www.odoo.com/documentation/19.0/developer/reference/external_rpc_api.html).

### XML-RPC availability

XML-RPC (`/xmlrpc/2/common`, `/xmlrpc/2/object`) is available on **every
Odoo version since 8.0** (2014). It is the most universal external API.
The `/json/2` endpoint (used as a faster fallback by the client) requires
the `commons_odoo` module and is not always present — the client already
falls back to XML-RPC transparently.

### Version detection at startup

```python
def _detect_version(o):
    """Detect Odoo version at connection time."""
    info = o.info()  # /xmlrpc/2/common → version() via info()
    serie = str(info.get("odoo_version", ""))  # "18.0", "17.0", etc.
    major = int(serie.split(".")[0]) if serie else 0
    return {"serie": serie, "major": major,
            "server_version": info.get("server_version", "")}
```

### Known field differences (16/17 → 18)

| Model | Odoo ≤17 | Odoo 18 | Impact |
|---|---|---|---|
| `account.account` | `user_type_id` (M2O) | `account_type` (selection string) | Wrong field → silent `None` |
| `account.account` | `company_id` (M2O) | `company_ids` (M2M) | Single-company assumption breaks |
| `account.journal` | `payment_debit_account_id`, `payment_credit_account_id` | **removed** — moved to `account.payment.method.line` | Field missing → `KeyError` |
| `account.bank.statement.line` | `to_process` | **removed** — use `state` + `is_reconciled` | Wrong filter → 0 results |
| `account.move` | `payment_state` has 5 values | `payment_state` has **7 values** (`reversed`, `blocked` added) | Wrong enum → miscount |

### Adaptation strategy

```python
FIELD_MAP = {
    # model: {old_field: new_field}
    "account.account": {
        "user_type_id": "account_type",     # ≤17 → 18
        "company_id":   "company_ids",
    },
}

def _adapt_fields(model, fields, major):
    """Translate field names for the Odoo version."""
    if major >= 18:
        return fields  # native 18, no translation
    mapping = FIELD_MAP.get(model, {})
    # Reverse: if caller asks for account_type, map back to user_type_id
    reverse = {v: k for k, v in mapping.items()}
    return [reverse.get(f, f) for f in fields]
```

### Version matrix

| Odoo | Support level | Auth | Transport | Notes |
|---|---|---|---|---|
| ≤13 | **Unsupported** | password only | XML-RPC ✅ | Security: no API keys |
| 14–15 | **Best-effort** | API Key ✅ | XML-RPC ✅ | Field maps untested |
| 16–17 | **Supported** | API Key ✅ | XML-RPC ✅ | Field adaptation active |
| **18** | **Primary target** | API Key ✅ | XML-RPC ✅ + JSON-2 ✅ | All 12 patterns verified |
| 19 | **Forward-compatible** | API Key + Bearer | XML-RPC ✅ + JSON-2 ✅ | Not tested, should work |

---

## 8. Transport Strategy

Source: [MCP 2026-07-28 transports](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)

### Two transports, both supported

| | **stdio** (v1, default) | **Streamable HTTP** (v2, optional) |
|---|---|---|
| How | Host spawns the server as a subprocess | Server runs as independent HTTP process |
| Credentials | Local env vars, never leave the machine | OAuth for MCP layer + Odoo creds injected |
| Sessions | Stateless (2026-07-28 removed session IDs) | Stateless — every POST is autonomous |
| Use case | Personal use, per-user Odoo instance | Team/company sharing one Odoo instance |
| `server.json` | `"transport": {"type": "stdio"}` | `"transport": {"type": "http", "url": "..."}` |

### stdio (v1 — primary)

**For v1, stdio is the default and only published transport.** Reasons:

1. Odoo credentials are per-user → must stay local
2. No server to deploy, no port to manage
3. Host manages process lifecycle
4. Works with every MCP host (Claude Desktop, Cursor, Hermes)

```python
mcp.run(transport="stdio")
```

### Streamable HTTP (v2 — when and how)

For teams that share an Odoo instance behind a VPN, or for deployment on
a company server. The 2026-07-28 spec simplified this significantly:
sessions were removed, so the server is fully stateless.

```python
mcp.run(transport="http", host="0.0.0.0", port=8080)
```

Security requirements per spec:
- **MUST** validate the `Origin` header on all connections (DNS rebinding protection)
- **MUST** use OAuth 2.1 Protected Resource Metadata (RFC 9728)
- **MUST** support client registration via URL-based Client ID Metadata

The `server.json` would add a `remotes` entry:

```json
{
  "packages": [{
    "transport": {
      "type": "http",
      "url": "https://odoo-assistant.company.internal/mcp"
    }
  }]
}
```

Source: [registry/remote-servers](https://modelcontextprotocol.io/registry/remote-servers)

This is **not in scope for v1** because it introduces auth concerns (OAuth
for the MCP layer + Odoo credentials) that would complicate the initial
release. It is milestone M7.

---

## 9. Repository Structure

### GitHub repository: `crottolo/odoo-assistant-mcp`

```
odoo-assistant/
├── pyproject.toml              # uv/pip/hatch build config
├── server.json                 # MCP Registry manifest
├── README.md                   # English, for PyPI + GitHub
├── LICENSE                     # MIT
├── .github/
│   └── workflows/
│       ├── tests.yml           # CI: run tests on every PR
│       └── publish.yml         # CD: tag → PyPI → MCP Registry
├── src/
│   └── odoo_assistant/
│       ├── __init__.py         # MCPServer init, tool registration
│       ├── server.py           # main entry point (mcp.run)
│       └── odoo_scripts/       # bundled Odoo scripts (from skill)
│           ├── __init__.py
│           ├── odoo_client.py
│           ├── safety_layer.py
│           ├── write_patterns.py
│           ├── documents.py
│           ├── collaboration.py
│           ├── census.py
│           ├── query.py
│           ├── view_first.py
│           └── explore_module.py
├── references/                 # SKILL.md + 23 reference docs
│   ├── SKILL.md
│   ├── writing.md
│   ├── payments.md
│   ├── deletion.md
│   ├── documents.md
│   ├── collaboration.md
│   ├── recipes.md
│   └── ... (remaining references)
├── tests/
│   ├── test_tools.py           # MCP tool tests (mock Odoo)
│   ├── test_safety.py          # safety layer tests
│   ├── test_integration.py     # live Odoo tests (marked @pytest.mark.live)
│   └── conftest.py             # fixtures, mock Odoo
└── docs/
    ├── ARCHITECTURE.md
    ├── CONTRIBUTING.md
    └── CHANGELOG.md
```

### Key decisions

1. **The Odoo scripts are bundled inside the package** — not a dependency.
   This keeps them synchronized with the safety layer and write patterns.

2. **References are bundled** — they're served as MCP Resources AND available
   in the GitHub repo for skill-based agents (Hermes, Claude Code).

3. **The repo IS the PyPI package source.** No separate package repo.

### Relationship to the existing skill

```
~/.agents/skills/odoo/          ← skill (Hermes, Claude Code)
    scripts/    ← same code
    references/ ← same docs

crottolo/odoo-assistant-mcp               ← MCP server (PyPI, Registry)
    src/odoo_assistant/odoo_scripts/   ← bundled copy of scripts
    references/                  ← bundled copy of refs
```

**Maintenance strategy:** the skill stays at `~/.agents/skills/odoo/` as the
canonical source. A script (`sync-from-skill.sh`) copies scripts and
references to the MCP repo before each release. This avoids a monorepo while
keeping them synchronized.

---

## 10. Build and Packaging

### pyproject.toml

```toml
[project]
name = "odoo-assistant"
version = "1.0.0"
description = "An Odoo virtual employee via MCP: query, create, modify records, run workflows"
readme = "README.md"
license = { text = "MIT" }
requires-python = ">=3.10"
authors = [
    { name = "Roberto Crotti", email = "bo@fl1.cz" }
]
keywords = ["mcp", "odoo", "erp", "model-context-protocol", "claude", "ai"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Software Development :: Libraries",
]
dependencies = [
    "mcp[cli]>=2.0.0,<3",
]

[project.urls]
Homepage = "https://github.com/crottolo/odoo-assistant-mcp"
Repository = "https://github.com/crottolo/odoo-assistant-mcp"
Issues = "https://github.com/crottolo/odoo-assistant-mcp/issues"
MCPRegistry = "https://registry.modelcontextprotocol.io/servers/io.github.crottolo/odoo-assistant"

[project.scripts]
odoo-assistant = "odoo_assistant.server:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/odoo_assistant"]
```

### Install methods

| Method | Command | Use case |
|--------|---------|----------|
| uvx (recommended) | `uvx odoo-assistant` | No install, host runs directly |
| pip | `pip install odoo-assistant` | Traditional |
| from source | `git clone && uv run` | Development |

### Entry point

```python
# src/odoo_assistant/server.py
import sys
import os

from mcp.server import MCPServer

mcp = MCPServer("odoo-assistant")

# ... tool definitions ...

def main():
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()
```

---

## 11. Publishing Pipeline

### Overview

```
Code → Tests pass → Tag v1.0.0 → GitHub Actions:
  PyPI publish (twine/uv publish) → MCP Registry publish (mcp-publisher)
```

### Step-by-step publishing flow

Source: [registry/quickstart](https://modelcontextprotocol.io/registry/quickstart),
[registry/github-actions](https://modelcontextprotocol.io/registry/github-actions)

#### 11.1 PyPI publication

```bash
# Build
uv build  # creates dist/odoo_assistant-1.0.0-py3-none-any.whl

# Publish
uv publish  # or: twine upload dist/*
```

#### 11.2 MCP Registry publication

**Namespace:** `io.github.crottolo/odoo-assistant`
(reverse-DNS, verified via GitHub OAuth)

**Ownership verification for PyPI:**
The README.md must contain an HTML comment with the server name:

```markdown
<!-- mcp-name: io.github.crottolo/odoo-assistant -->
```

This string is checked by the Registry against the PyPI package README.

**server.json:**

```json
{
  "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
  "name": "io.github.crottolo/odoo-assistant",
  "title": "Odoo MCP",
  "description": "An Odoo virtual employee via MCP: query, create, modify records, run workflows, generate PDFs, manage activities.",
  "repository": {
    "url": "https://github.com/crottolo/odoo-assistant-mcp",
    "source": "github"
  },
  "version": "1.0.0",
  "packages": [{
    "registryType": "pypi",
    "identifier": "odoo-assistant",
    "version": "1.0.0",
    "transport": { "type": "stdio" },
    "environmentVariables": [
      {
        "name": "ODOO_BASE_URL",
        "description": "Your Odoo instance URL (e.g. http://localhost:8069 or https://mycompany.odoo.com)",
        "isRequired": true,
        "isSecret": false,
        "format": "string"
      },
      {
        "name": "ODOO_DB",
        "description": "Odoo database name",
        "isRequired": true,
        "isSecret": false,
        "format": "string"
      },
      {
        "name": "ODOO_USER",
        "description": "Odoo login username",
        "isRequired": true,
        "isSecret": false,
        "format": "string"
      },
      {
        "name": "ODOO_API_KEY",
        "description": "Odoo API key (Settings > Users > API Keys > New API Key)",
        "isRequired": true,
        "isSecret": true,
        "format": "string"
      }
    ]
  }]
}
```

#### 11.3 GitHub Actions workflow

```yaml
# .github/workflows/publish.yml
name: Publish to PyPI and MCP Registry

on:
  push:
    tags: ["v*"]

permissions:
  id-token: write
  contents: read

jobs:
  pypi:
    name: Publish package to PyPI
    runs-on: ubuntu-latest
    steps:
      - name: Check out repository
        uses: actions/checkout@v4

      - name: Set up uv
        uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.12"
          enable-cache: true

      - name: Install dependencies
        run: uv sync

      - name: Run non-live tests
        run: uv run pytest tests/ -m "not live"

      - name: Build package
        run: uv build

      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1

  registry:
    name: Publish server to MCP Registry
    needs: pypi
    runs-on: ubuntu-latest
    steps:
      - name: Check out repository
        uses: actions/checkout@v4

      - name: Install mcp-publisher
        shell: bash
        run: |
          curl -L "https://github.com/modelcontextprotocol/registry/releases/latest/download/mcp-publisher_$(uname -s | tr '[:upper:]' '[:lower:]')_$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/').tar.gz" | tar xz mcp-publisher

      - name: Authenticate to MCP Registry
        run: ./mcp-publisher login github-oidc

      - name: Publish to MCP Registry
        run: ./mcp-publisher publish
```

### Versioning

Source: [registry/versioning](https://modelcontextprotocol.io/registry/versioning)

- SemVer: `MAJOR.MINOR.PATCH`
- Version in `server.json` matching the PyPI version is a best-practice, not a strict MUST.
- Once published, a version **cannot be changed** — must publish a new one
- Registry sorts by SemVer; the highest is marked "latest"

---

## 12. Testing Strategy

### Test pyramid

```
                    ┌───────────┐
                    │  E2E (5)  │   ← live Odoo, real MCP host
                    └─────┬─────┘
                  ┌───────┴───────┐
                  │ Integration   │   ← live Odoo, no host
                  │    (15)       │
                  └───────┬───────┘
                ┌─────────┴─────────┐
                │   Unit (50+)      │   ← mock Odoo, fast
                └───────────────────┘
```

### 12.1 Unit tests (mock Odoo)

**Goal:** every tool, every safety path, fast feedback.

| Suite | What it tests | Count |
|-------|---------------|-------|
| `test_tools.py` | Tool registration, schema generation, input validation | 12 tools × 2 = 24 |
| `test_safety.py` | L0–L5 classification, blocked paths, configurable max level | 15 |
| `test_connection.py` | Credential validation, missing env vars, connection errors | 6 |
| `test_formatting.py` | Output formatting (JSON, text, error messages) | 5 |

```python
# Example unit test
def test_search_read_calls_odoo():
    mock_odoo = MockOdoo()
    mock_odoo.set_results("sale.order", [{"id": 1, "name": "SO001"}])

    result = search_read(
        model="sale.order",
        domain=[["state", "=", "sale"]],
        fields=["name", "state"],
        limit=10
    )

    assert "SO001" in result
    assert mock_odoo.last_call["model"] == "sale.order"
    assert mock_odoo.last_call["domain"] == [["state", "=", "sale"]]
```

### 12.2 Integration tests (live Odoo, marked)

**Goal:** the tools work against a real Odoo instance.

Marked with `@pytest.mark.live` — only run when `ODOO_BASE_URL` is set.

| Test | What it verifies |
|------|------------------|
| `test_live_connection` | Connect, authenticate, get uid |
| `test_live_search` | search_read on sale.order, verify results |
| `test_live_safety` | Write blocked by L4/L5 |
| `test_live_write` | Create → read → verify → cleanup |
| `test_live_workflow` | Create order → confirm → invoice → cleanup |

```python
@pytest.mark.live
def test_live_workflow():
    """Full workflow: create partner → create order → confirm → cleanup."""
    o = _get_odoo()

    # Create test partner
    partner_id = o.call("res.partner", "create", [{"name": "MCP Test Partner"}])[0]
    try:
        # Create order
        order_id = w.create("sale.order", {...}, unique_on=["name"])
        # Confirm
        w.act("sale.order", "action_confirm", [order_id], watch="state")
        assert w.state_of("sale.order", order_id)["state"] == "sale"
    finally:
        o.call("res.partner", "unlink", [[partner_id]])
```

### 12.3 E2E tests (MCP host)

**Goal:** the server works inside a real host.

| Test | How |
|------|-----|
| Claude Desktop | Manual: add config, call tool, verify result |
| MCP Inspector | Automated: `mcp-inspector odoo-assistant` → call tools |
| Hermes | `hermes mcp add` → chat → verify |

Source: [MCP Inspector](https://modelcontextprotocol.io/docs/2026-07-28/tools/inspector)

### 12.4 MCP Inspector (automated protocol test)

```bash
# Install inspector
npx @modelcontextprotocol/inspector

# Point at our server
mcp-inspector uvx odoo-assistant

# Scripted tests
mcp-inspector uvx odoo-assistant --test tools/list
mcp-inspector uvx odoo-assistant --test tools/call --name search_read \
  --input '{"model":"sale.order","domain":[],"fields":["name"],"limit":3}'
```

### 12.5 Acceptance tests (the 12 cold-start scenarios)

The 12 cold-start runs from skill development become automated acceptance
tests:

| Run | Scenario | Verifies |
|-----|----------|----------|
| 1–6 | Read queries | search_read, count, instance overview |
| 7–9 | Custom module discovery | explore_module on in-house modules |
| 10–12 | Write workflows | create → confirm → invoice → verify |

---

## 13. Documentation Plan

### 13.1 README.md (PyPI + GitHub)

The README is the first thing users see on PyPI and GitHub. It must contain:

- The `mcp-name` HTML comment for Registry verification
- Quick start (3 steps: install, configure, use)
- Screenshots of it working in Claude Desktop
- Configuration examples for each host
- Safety layer explanation
- Link to full docs

### 13.2 Configuration examples per host

#### Claude Desktop

```json
{
  "mcpServers": {
    "odoo-assistant": {
      "command": "uvx",
      "args": ["odoo-assistant"],
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

#### ChatGPT / OpenAI

Via Custom Connectors:
1. Go to Settings → Connectors → Add Connector
2. Enter server URL or select from Registry
3. Enter Odoo credentials when prompted

#### Cursor

```bash
# .cursor/mcp.json
{
  "odoo-assistant": {
    "command": "uvx",
    "args": ["odoo-assistant"],
    "env": { "ODOO_BASE_URL": "...", ... }
  }
}
```

#### Hermes

```bash
hermes mcp add odoo-assistant \
  --env ODOO_BASE_URL=https://mycompany.odoo.com \
         ODOO_DB=mycompany ODOO_USER=admin ODOO_API_KEY=xxx \
  --args run odoo-assistant
```

#### VS Code Copilot

```json
// settings.json
{
  "mcp.servers": {
    "odoo-assistant": {
      "command": "uvx",
      "args": ["odoo-assistant"],
      "env": { ... }
    }
  }
}
```

### 13.3 Security documentation

A dedicated `SECURITY.md` explaining:
- What data the server can access (everything the Odoo user can)
- What operations are blocked (L4/L5)
- How credentials flow (env vars only)
- How to report security issues

---

## 14. Operational Runbook

### 14.1 Release procedure

```
1. Update version in pyproject.toml AND server.json
2. Run tests: uv run pytest tests/ -m "not live"
3. Run live tests: uv run pytest tests/ -m "live" (needs Odoo)
4. Commit: git commit -am "v1.0.0"
5. Tag: git tag v1.0.0
6. Push: git push origin main --tags
7. GitHub Actions triggers:
   a. PyPI publish
   b. MCP Registry publish
8. Verify: search for "odoo" at registry.modelcontextprotocol.io
9. Test install: uvx odoo-assistant (fresh machine)
```

### 14.2 Sync from skill

When the skill at `~/.agents/skills/odoo/` is updated:

```bash
# scripts/sync-from-skill.sh
SKILL_DIR=~/.agents/skills/odoo
MCP_DIR=~/VSC/odoo-assistant-mcp

cp "$SKILL_DIR/scripts/"*.py "$MCP_DIR/src/odoo_assistant/odoo_scripts/"
cp "$SKILL_DIR/references/"*.md "$MCP_DIR/references/"
cp "$SKILL_DIR/SKILL.md" "$MCP_DIR/references/"

echo "Synced. Review diff, then commit."
```

### 14.3 Rollback

```bash
# PyPI: cannot unpublish, but can yank
pip install odoo-assistant==1.0.0  # pin to old version

# MCP Registry: publish a new version with a fix
# The old version remains visible but "latest" points to the new one
```

### 14.4 Monitoring

The MCP server logs to stderr (required by stdio spec). The host captures it:

- Claude Desktop: `~/Library/Logs/Claude/mcp-server-odoo-assistant.log`
- Hermes: `~/.hermes/logs/mcp-odoo-assistant.log`
- Cursor: VS Code output panel

### 14.5 Troubleshooting

| Symptom | Check |
|---------|-------|
| "Connection refused" | `ODOO_BASE_URL` correct? Odoo running? |
| "Access denied" | `ODOO_API_KEY` valid? User active? |
| "Database does not exist" | `ODOO_DB` name exact? |
| Server not showing in host | Config JSON syntax, absolute path, restart host |
| Tool calls fail silently | Check host logs for stderr output |
| "BLOCKED by safety layer" | Expected for L4/L5. Check `ODOO_MCP_MAX_LEVEL` |

---

## 15. Milestone Plan

### M0 — Prototype (1 day)

- [ ] Create `crottolo/odoo-assistant-mcp` GitHub repo
- [ ] `pyproject.toml` with hatchling
- [ ] Copy scripts into `src/odoo_assistant/odoo_scripts/`
- [ ] Write `server.py` with 5 basic tools (search_read, read, count, write, run_action)
- [ ] Test locally with `uv run`
- [ ] Connect from Claude Desktop

**Deliverable:** working prototype, 5 tools, local test.

### M1 — Full tool set (2 days)

- [ ] Implement all 12 tools
- [ ] Add resources (SKILL.md + references)
- [ ] Add safety layer integration to every write tool
- [ ] Write unit tests (mock Odoo)
- [ ] Test with MCP Inspector

**Deliverable:** all tools working, unit tests green.

### M2 — Live integration test (1 day)

- [ ] Point at dev Odoo instance (`dev8069:8069`)
- [ ] Run 12 cold-start scenarios as automated tests
- [ ] Test PDF generation, notifications, activities
- [ ] Document any new discoveries

**Deliverable:** live tests green, no data corruption.

### M3 — Package and publish (1 day)

- [ ] Write README.md with `mcp-name` comment
- [ ] Create `server.json`
- [ ] Set up PyPI account / token
- [ ] `uv build && uv publish`
- [ ] `mcp-publisher login github-oidc`
- [ ] `mcp-publisher publish`
- [ ] Verify in Registry

**Deliverable:** `uvx odoo-assistant` works from a clean machine.

### M4 — Multi-host testing (1 day)

- [ ] Test in Claude Desktop
- [ ] Test in ChatGPT (Custom Connector)
- [ ] Test in Cursor
- [ ] Test in Hermes
- [ ] Document per-host config in README

**Delexiverable:** works in all major hosts.

### M5 — CI/CD (0.5 day)

- [ ] Create `.github/workflows/tests.yml` (PR checks)
- [ ] Create `.github/workflows/publish.yml` (tag → publish)
- [ ] Test the pipeline with a patch release

**Deliverable:** automated releases.

### M6 — Polish and docs (1 day)

- [ ] Full README with screenshots
- [ ] CONTRIBUTING.md
- [ ] CHANGELOG.md
- [ ] SECURITY.md
- [ ] Record demo video / GIF

**Deliverable:** publication-ready.

**Total estimated effort: ~7.5 days**

---

## 16. Risk Register

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|------------|--------|------------|
| R1 | Odoo API key has admin access → destructive ops | Medium | High | Safety layer blocks L4/L5; `ODOO_MCP_MAX_LEVEL` env var |
| R2 | MCP Registry breaks in preview | Medium | Low | Package still works via `uvx`/`pip` without Registry |
| R3 | MCP Python SDK API changes | Low | Medium | Pin `mcp[cli]>=2.0.0,<3` |
| R4 | Users expose Odoo to internet for remote MCP | Low | High | Document stdio-only; warn about remote in README |
| R5 | Safety layer too restrictive → user frustration | Medium | Low | Configurable max level; clear error messages |
| R6 | Token leak in git history | Low | Critical | Pre-commit hook scanning for hex strings |
| R7 | Odoo version differences (16/17 vs 18) | Medium | Medium | Document as Odoo 18 only; note in README |
| R8 | `mcp-publisher` CLI unavailable/broken | Low | Low | Manual REST API fallback documented |
| R9 | Large output overwhelms host context | Medium | Medium | Cap tool output at 5000 chars; truncate with notice |
| R10 | ChatGPT MCP support differs from Claude | Medium | Low | Test both early in M4 |

---

## 17. Open Questions

| # | Question | Who decides | Default if unresolved |
|---|----------|-------------|----------------------|
| Q1 | Should we support read-only mode by default (L0 only)? | User | No — L2 default, configurable |
| Q2 | License: MIT or Apache-2.0? | User | MIT (skill already MIT) |
| Q3 | Should references be resources or prompts? | Technical | Resources (read-only, discoverable) |
| Q4 | Package name on PyPI: `odoo-assistant` or `odoo18-assistant`? | User | `odoo-assistant` (not version-locked) |
| Q5 | Should we support Odoo.com SaaS (cloud) URLs? | Technical | Yes — `ODOO_BASE_URL` covers it |
| Q6 | Monetization: free or paid? | User | Free / MIT |
| Q7 | Should Streamable HTTP ship in v1 or v2? | User | v2 (M7) — stdio covers all v1 use cases |

---

## 18. Skills over MCP — Migration Roadmap

Source: [Skills Over MCP WG charter](https://modelcontextprotocol.io/community/working-groups/skills-over-mcp),
[SEP-2640](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2640),
[experimental-ext-skills repo](https://github.com/modelcontextprotocol/experimental-ext-skills).

### Why this matters

The Odoo skill is structured exactly as the Skills Over MCP Working Group
envisioned: `SKILL.md` with non-negotiable rules, references on-demand,
safety layer, auto-discovery. Today we serve it as MCP **Resources**
(`odoo://skill`, `odoo://ref/*`). When the Skills Extension is approved,
the same content can be served as a first-class **Skill** primitive —
and MCP hosts will load it natively, the same way Hermes and Claude Code
do today.

### Current state of the Skills Extension

| Component | Status |
|---|---|
| SEP-2640 (Skills Extension, Extensions Track) | **In Review** |
| Reference implementation | **In Review** |
| `experimental-ext-skills` repo | Experimental |
| Python SDK support | Not yet |
| Any host support | Not yet |

**The extension is not production-ready.** We cannot ship against it today.

### Migration path

| Phase | When | What changes | What stays |
|---|---|---|---|
| **v1 (today)** | Now | References served as MCP Resources via `odoo://skill`, `odoo://ref/*` | All 12 tools, safety layer, auto-discovery |
| **v1.5** | SEP-2640 approved | Add `skills/list` + `skills/get` alongside resources (dual-serve) | Same content, two discovery paths |
| **v2** | SDK ships extension | Drop resources, serve as native Skills primitive | Tools, safety, discovery unchanged |

### What doesn't change in the migration

The **12 MCP tools** are not affected. Skills over MCP is about distributing
the *instructions* (how to reason about Odoo), not the *operations* (the
actual XML-RPC calls). An MCP host that loads the skill gets the 7
non-negotiable rules, the 12 write patterns, and the safety taxonomy —
then calls the same tools.

### Why we chose Resources for v1

1. Resources are in the **core spec** (stable since 2024-11-05)
2. Every MCP host already supports `resources/list` + `resources/read`
3. No extension negotiation needed
4. The migration to Skills is additive (new endpoint, same content)

---

## 19. Self-Evolution Protocol — Auto-Discovering New Modules

The defining capability of Odoo Assistant. Every other Odoo MCP server is
static: it exposes hardcoded tools and breaks when the instance changes.
Odoo Assistant **learns the instance it's connected to** and writes new
knowledge for future queries.

### The problem

Odoo instances are customized. Every company installs custom modules, adds
fields via Studio, renames workflows. A server that only knows `sale.order`
and `account.move` is useless on an instance with `superchat.message`,
`3cx.call`, or `preinvoice.batch`.

### What already exists

`explore_module.py` already does this for the skill:

```
python3 explore_module.py helpdesk
  → generates references/helpdesk.md with:
    - 53 records, 6 stages, 2 teams
    - state_id: New(3) In Progress(1) On Hold(2) Solved(45) Cancelled(2)
    - 14 action_* methods from form views
    - volume per model
```

The reference is written to disk and available on the next query. This
happened 11 times for Persevida's custom modules — each generated by
interrogation, never written by hand.

### The MCP self-evolution protocol

When the MCP server encounters a model it doesn't have a reference for:

```
┌────────────────────────────────────────────────────────────┐
│  1. DETECT                                                 │
│     Agent calls search_read on an unknown model            │
│     → server checks: does references/<model>.md exist?     │
│     → NO → trigger discovery                               │
│                                                            │
│  2. DISCOVER (view-first)                                  │
│     → list models matching the module prefix               │
│     → for each model: fields_get() → field types, states   │
│     → resolve menu tree → find views → extract actions     │
│     → search_count() per model → volumes                   │
│     → state fields with distribution (stage-like guard)    │
│                                                            │
│  3. WRITE                                                  │
│     → generate references/<module>.md (≤12 KB, with cap)   │
│     → write to the package data directory                  │
│     → register as MCP resource: odoo://ref/<module>        │
│                                                            │
│  4. SERVE                                                  │
│     → next query for that module reads from the reference  │
│     → no re-discovery needed                               │
│     → reference persists across server restarts            │
└────────────────────────────────────────────────────────────┘
```

### MCP tool

```python
@mcp.tool()
async def explore_module(
    module_name: str,                    # "helpdesk", "superchat"
    action: str = "generate"             # "list" | "generate"
) -> str:
    """Discover a module's structure from the live instance.

    For 'list': returns candidate modules ranked by data volume.
    For 'generate': interrogates the instance and creates a reference
    document. The reference persists and is available to all future
    queries via the odoo://ref/<module> resource.

    This is how Odoo Assistant learns new modules without code changes."""
```

### What the reference contains (generated, never hand-written)

Each generated reference includes:

| Section | Source | Example |
|---|---|---|
| Module info | `ir.module.module` | name, description, author |
| Models & volumes | `search_count` per model | `superchat.message: 2006` |
| State fields | `fields_get` + `search_read` distribution | `state: sent(1894) failed(7) pending(105)` |
| Actions | form view XML → `action_*` methods | `action_send`, `action_retry` |
| Menus | menu tree → view resolution | SuperChat → Messages → Sent |
| Warnings | anomalous distributions | "854/2057 attendees never responded" |

### Persistence and upgrade safety

```
Package data dir (read-write):
  ~/.local/share/odoo-assistant/references/

  ├── helpdesk.md          (generated 2026-08-11)
  ├── superchat.md         (generated 2026-08-11)
  ├── evolution.md         (generated 2026-08-11)
  └── ...                  (regenerated after Odoo upgrades)
```

- References are **regenerated**, not appended (two-half write: new content
  above `## NOTES`, preserved notes below)
- The `## NOTES` section survives regeneration — manual annotations persist
- After an Odoo upgrade, calling `explore_module` again refreshes the
  reference with new fields/states/actions automatically

### Versioned knowledge base

The generated references form a **versioned knowledge base**:

```python
@mcp.tool()
async def list_known_modules() -> str:
    """List all modules Odoo Assistant has learned.
    Returns module name, generation date, and record count."""
```

This means an agent can ask "what modules do you know?" and get a
data-driven answer — not "sale.order, account.move" hardcoded, but the
actual list of modules this specific instance uses.

### Why this is unique

| Feature | Other Odoo MCP servers | Odoo Assistant |
|---|---|---|
| Knows `sale.order` | ✅ (hardcoded) | ✅ (discovered) |
| Knows custom modules | ❌ | ✅ (auto-discovered) |
| Adapts after Odoo upgrade | ❌ (breaks) | ✅ (regenerates) |
| State values from data | ❌ (guessed) | ✅ (measured distribution) |
| View-first navigation | ❌ | ✅ |
| Knows what it doesn't know | ❌ | ✅ (`list_known_modules`) |

---

## Appendix A: Source documentation

All 144 `.md` files from the MCP spec are cached at:
`~/VSC/ODOO-HERMES/docs/mcp-spec/`

Key references:
- Specification: `specification/2026-07-28/`
- Registry: `registry/`
- Extensions: `extensions/`
- Tutorial: `docs/2026-07-28/develop/build-server.md`
- SEPs: `seps/`

---

## Appendix B: Existing skill inventory

| Component | Location | Lines | Status |
|-----------|----------|-------|--------|
| SKILL.md | `references/SKILL.md` | 400 | v1.2.0 |
| odoo_client.py | `odoo_scripts/` | 120 | Stable |
| safety_layer.py | `odoo_scripts/` | 150 | Stable, 11/11 tests |
| write_patterns.py | `odoo_scripts/` | 200 | Stable, Writer class |
| documents.py | `odoo_scripts/` | 180 | Stable |
| collaboration.py | `odoo_scripts/` | 220 | Stable |
| census.py | `odoo_scripts/` | 100 | Stable |
| query.py | `odoo_scripts/` | 80 | Stable |
| view_first.py | `odoo_scripts/| | 90 | Stable |
| explore_module.py | `odoo_scripts/` | 150 | Stable |
| 23 references | `references/` | ~5000 | Stable |

---

*PRD version 1.0 · 2026-08-13 · Roberto Crotti*
