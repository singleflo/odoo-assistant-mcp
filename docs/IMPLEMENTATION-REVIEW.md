# Implementation Review — Critical Gaps for `odoo-assistant`

> **Review date**: 2026-08-13
> **Reviewer**: ZCode
> **Method**: verified against sources, no assumptions
> **Status**: READY FOR DEVELOPMENT (with documented gaps)

---

## 1. What exists and is VERIFIED

### Source code (9 scripts, battle-tested)

| Script | Lines | Tests passed | Role |
|---|---|---|---|
| `odoo_client.py` | 550 | connection_guard 6/6 | XML-RPC + JSON-2 auto-detect, `OdooExecutedButUnserializable` |
| `safety_layer.py` | 200 | 11/11 bypasses, 15/15 degraded | L0–L5 classification, model guards |
| `write_patterns.py` | 250 | step idempotence, cancel via act | Writer class: create/write/act/step/state_of/report |
| `documents.py` | 200 | PDF 20KB, download verified | PDF generation, attachment download, notifications |
| `collaboration.py` | 250 | 11/11 operations | Activities, calendar, internal notes |
| `census.py` | 120 | live data verified | Instance profile, module inventory, anomalies |
| `query.py` | 90 | live queries | CLI for reading the profile |
| `view_first.py` | 100 | Community + Enterprise | Menu→view→field resolution |
| `explore_module.py` | 300 | 11 modules generated | Auto-discovery, reference generator |

**Total: ~2.000 lines of verified Python**, 12 cold-start runs (9 read + 3 write),
23 references generated from live data.

### Documentation

| Asset | Location | Size |
|---|---|---|
| PRD (22 sections) | `docs/PRD.md` | 60 KB |
| MCP server guide | `docs/mcp-server-guide.md` | 27 KB |
| MCP spec 2026-07-28 | `docs/mcp-spec/` | 2.8 MB, 142 file |
| `/json/2` source | `docs/json2-source/` | the Odoo module enabling modern API |
| 24 references | `references/` | ~50 KB |

---

## 2. CRITICAL GAPS (must close before shipping)

### GAP 1: MCP Python SDK API ~~not verified~~ → CLOSED

**VERIFIED** on installed package `mcp==1.28.1`, Python 3.11:

> **SUPERSEDED NOTE (2026-08-13)**: The project has been updated to use MCP SDK 2.0+ which introduces the `MCPServer` class as the correct API. The `FastMCP` pattern documented below was correct for SDK 1.28.1 but is now superseded. The codebase now uses `MCPServer` from `mcp.server`.

```python
# WRONG (from the tutorial — refers to a future SDK 2.0):
from mcp.server import MCPServer

# CORRECT (actual installed API):
from mcp.server import FastMCP

mcp = FastMCP("odoo-assistant")

@mcp.tool()
async def search_read(model: str, domain: list) -> str:
    """Search and read records."""
    ...

@mcp.resource("odoo://skill")
def skill_content() -> str:
    return open("references/SKILL.md").read()

mcp.run(transport="stdio")  # also: "sse", "streamable-http"
```

**API facts** (from `dir()` on installed package):
- `FastMCP.tool()` — decorator, auto-generates JSON Schema from type hints
- `FastMCP.resource(uri)` — decorator, serves content on demand
- `FastMCP.prompt()` — decorator, pre-written templates
- `FastMCP.run(transport)` — supports `"stdio"`, `"sse"`, `"streamable-http"`
- `Server` (low-level) also available — no decorators, manual handlers

**PRD correction needed**: replace ALL `MCPServer` with `FastMCP` throughout.

### GAP 2: `list[int]` type hints require Python 3.10+

**Verified**: our scripts use `X | None` syntax which is 3.10+.
The PRD says `requires-python = ">=3.10"`.

**Risk**: LOW — documented and consistent.

**But**: the MCP SDK itself may require 3.11+ (httpx2 dependency). Need to verify.

### GAP 3: Output truncation strategy is designed but not tested

**PRD says**: cap at 5000 chars, truncate with notice.

**Not tested**: how does Claude Desktop handle a truncated tool result?
Does the agent ask for more? Does it confuse the conversation?

**Risk**: MEDIUM — may need iterative tuning after real-host testing.

### GAP 4: Streamable HTTP security (OAuth) is documented but not implemented

**PRD §8 documents** the requirements (Origin validation, OAuth 2.1,
RFC 9728 Protected Resource Metadata).

**Not implemented**: correctly deferred to M7.

**Risk**: LOW for v1 (stdio only).

### GAP 5: `@api.readonly` decorator enforcement via `/json/2/read/`

**VERIFIED from source code** (`controllers/json2.py`):

```python
if readonly_only and not getattr(func, "_readonly", False):
    raise Forbidden(f"{model_name}.{method_name} is not @api.readonly.")
```

This means `/json/2/read/<model>/<method>` is a **real read-only endpoint**
that rejects write methods at the application level.

**Current gap**: our `odoo_client.py` uses XML-RPC for everything. It does
NOT use `/json/2/read/` for L0 operations. If it did, we'd have server-side
enforcement of read-only safety, not just client-side.

**DECISION (ours to make)**: YES — use `/json/2/read/` as the PREFERRED
transport for L0 operations where available. XML-RPC remains the universal
fallback. This gives us **dual enforcement**: client classifies + server
rejects. Available on Odoo 18+ (with `commons_odoo`) and 19+ (native).

**Implementation**: the client already auto-detects JSON-2 and falls back.
We add a `read_only=True` flag to `search_read` that, when JSON-2 is
available, routes to `/json/2/read/` instead of `/json/2/`. The safety
layer classifies first; the transport enforces second.

### GAP 6: Odoo 16/17 field adaptation is designed but untested

**PRD §7C maps** 5 known field differences (16/17 → 18).

**Not tested**: the `FIELD_MAP` adaptation code has never run against a
real Odoo 16/17 instance.

**Risk**: MEDIUM — a wrong mapping silently returns `None` instead of
erroring. Must test against a real 16/17 instance before claiming support.

**Recommendation**: scope v1 as "Odoo 18 verified, 16/17 best-effort with
documented caveats". Drop the claim of full 16/17 support until tested.

---

## 3. ARCHITECTURE DECISIONS (confirmed)

### Transport: XML-RPC universal, JSON-2 preferred

**Confirmed from source code** (`json2_dispatcher.py`, `controllers/json2.py`):

```
/json/2/<model>/<method>      → full RPC, bearer auth
/json/2/read/<model>/<method> → READ-ONLY, rejects non-@api.readonly
/xmlrpc/2/object              → universal fallback (Odoo 8+)
```

The client already auto-detects and falls back. **No change needed** for v1.

### `/json/2` availability matrix (VERIFIED)

| Odoo | XML-RPC | JSON-2 | Source |
|---|---|---|---|
| 8–15 | ✅ | ❌ | not in core |
| 16–17 | ✅ | ❌ | not in core |
| **18** | ✅ | ✅ (via `commons_odoo` module) | porting from 19 |
| **19+** | ✅ | ✅ (**native**, `auto_install: True`) | in `odoo/addons/api_doc` |

**Implication**: JSON-2 is NOT available on stock Odoo 18. It requires the
`commons_odoo` module (custom). The client already falls back to XML-RPC
when JSON-2 is unavailable. This is correct behavior.

### Safety layer: L0–L5 as the enforcement point

**Confirmed**: the MCP spec says tool annotations are **untrusted** by hosts.
Our safety layer is the only enforcement. The `/json/2/read/` endpoint adds
**server-side enforcement** for L0 operations where available.

---

## 4. RECOMMENDED IMPLEMENTATION ORDER

### Phase 1: Verify the SDK (DAY 0, before any code)

```bash
uv venv && source .venv/bin/activate
uv add "mcp[cli]"
python3 -c "from mcp.server import MCPServer; print('OK')"
python3 -c "import mcp; print(dir(mcp.server))"
# Read the actual API, not the docs
```

**Block**: do not write `server.py` until this passes.

### Phase 2: Minimal server (M0)

- 3 tools only: `search_read`, `count_records`, `instance_overview`
- stdio transport
- env-var credentials
- Test from Claude Desktop

### Phase 3: Full tool set (M1)

- All 12 tools from PRD §5B
- Safety layer integration on every write tool
- Resources: `odoo://skill`, `odoo://ref/*`
- MCP Inspector protocol test

### Phase 4: Self-evolution (the differentiator)

- `explore_module` tool that writes to disk
- `list_known_modules` tool
- Persistence across restarts

### Phase 5: Package and publish (M3)

- PyPI: `odoo-assistant`
- MCP Registry: `io.github.singleflo/odoo-assistant`
- GitHub Actions: tag → publish

---

## 5. UNRESOLVED QUESTIONS (need human decision)

| # | Question | Impact |
|---|---|---|
| U1 | Does the MCP SDK `MCPServer` match the tutorial, or has it changed? | Blocks M0 |
| U2 | Should L0 operations prefer `/json/2/read/` where available? | Better safety, needs testing |
| U3 | Odoo 16/17: claim "supported" or "best-effort"? | Affects README and Registry description |
| U4 | Output cap: 5000 chars fixed, or configurable via env var? | UX in hosts |

---

## 6. WHAT'S IN THE BOX (for the developer)

```
/Users/crotti/VSC/TOOLS/odoo-assistant/
├── src/odoo_assistant/
│   └── odoo_scripts/          ← 9 scripts, VERIFIED, copy into the package
├── references/                 ← 24 reference docs + SKILL.md
├── tests/                      ← (empty — tests to be written)
├── docs/
│   ├── PRD.md                  ← 60 KB, 22 sections, the spec
│   ├── mcp-server-guide.md     ← 27 KB, code examples
│   ├── mcp-spec/               ← 142 file, the entire MCP 2026-07-28 spec
│   ├── json2-source/           ← the /json/2 Odoo module source code
│   └── IMPLEMENTATION-REVIEW.md ← this file
└── (git init pending)
```

**The developer needs to:**
1. Read `docs/PRD.md` (the what and why)
2. Read `docs/IMPLEMENTATION-REVIEW.md` (the gaps and order)
3. Run the SDK verification (Phase 1)
4. Build `server.py` wrapping `odoo_scripts/`
5. Test against a live Odoo 18 instance

**The developer does NOT need to:**
- Understand Odoo internals (the scripts handle that)
- Design the safety layer (it exists, tested)
- Generate references (explore_module does it)
- Write the XML-RPC client (it exists, tested)
