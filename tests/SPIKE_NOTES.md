# SDK-verify spike — findings (plan todo 3)

Hard gate for Wave 2. **VERDICT: PASSED.** Everything below was observed live on
2026-08-13 against `mcp==2.0.0` / `mcp-types==2.0.0` in this repo's `.venv`
(Python 3.11.15). T7 (`server.py` skeleton) can rely on these facts without
re-deriving them.

Probe files kept as executable documentation: `tests/spike_server.py` (server),
`tests/spike_client.py` (scripted client + stdout capture). Neither is collected
by pytest (no `test_` prefix).

## 1. Verified API

```python
from mcp.server import MCPServer          # resolves to mcp.server.mcpserver.server
mcp = MCPServer("odoo-assistant")
mcp.run(transport="stdio")
```

`mcp.server.fastmcp` is gone in 2.0 — do not import it. Confirmed present on the
instance: `.tool()`, `.resource()`, `.prompt()`, `.run()`.

| Member | Signature (as introspected) |
|---|---|
| `.tool` | `(name=None, title=None, description=None, annotations=None, icons=None, meta=None, structured_output=None)` |
| `.resource` | `(uri, *, name=None, title=None, description=None, mime_type=None, icons=None, annotations=None, meta=None, security=None)` — **`uri` positional, everything else keyword-only** |
| `.prompt` | `(name=None, title=None, description=None, icons=None)` |
| `.run` | `(transport: Literal["stdio","sse","streamable-http"] = "stdio", **kwargs)` |

Other public members available for later waves: `add_tool`/`remove_tool`,
`add_resource`, `add_prompt`/`remove_prompt`, `list_tools`, `list_resources`,
`list_resource_templates`, `read_resource`, `call_tool`, `get_prompt`,
`completion`, `custom_route`, `middleware`, `settings`, `session_manager`,
`run_stdio_async`, `sse_app`, `streamable_http_app`.

## 2. `mcp-types` — NO pyproject change needed

`mcp-types==2.0.0` **is a separate distribution**, but it is a hard pinned
dependency of `mcp` (`Requires-Dist: mcp-types==2.0.0`), and `mcp/types/__init__.py`
re-exports it. Both of these work today:

```python
from mcp.types import TextContent   # -> <class 'mcp_types._types.TextContent'>
import mcp.types as types
```

So `dependencies = ["mcp[cli]>=2.0.0,<3"]` is sufficient — **do not** add
`mcp-types` to `pyproject.toml` (an unpinned second declaration could drift from
the `==` pin `mcp` enforces). Prefer importing via `mcp.types` for stability.

Full `mcp` runtime deps observed: `anyio`, `httpx2>=2.5.0`, `jsonschema`,
`mcp-types==2.0.0`, `opentelemetry-api>=1.28.0`, `pydantic>=2.12.0`, `pyjwt[crypto]`,
`python-multipart`, `sse-starlette`, `starlette`, `typing-extensions`,
`typing-inspection`, `uvicorn`; extras: `cli` -> `python-dotenv`, `typer`.

## 3. Result models are snake_case

SDK 2.0 result objects use Python field names, not the wire's camelCase:
`InitializeResult.protocol_version` (NOT `.protocolVersion` — raises
`AttributeError`). Same shape applies to `CallToolResult`, whose fields are
`meta, content, structured_content, is_error, result_type`. The JSON on the wire
is still camelCase (`structuredContent`, `isError`).

Negotiated protocol version on a local stdio round-trip: **2025-11-25**
(`LATEST_HANDSHAKE_VERSION`; `LATEST_MODERN_VERSION` is `2026-07-28`).

## 4. stdout purity — clean, and the gate proves it

Round-trip captured with the server behind `tee`
(`uv run python tests/spike_client.py --tee /tmp/spike_stdout.log`): stdout
contained **exactly 3 lines, all valid JSON-RPC** (`initialize`, `tools/list`,
`tools/call`). Server stderr on a clean run: **0 bytes** — no banner of any kind.

```bash
uv run python tests/spike_client.py --tee /tmp/spike_stdout.log
uv run python -c "import json; [json.loads(l) for l in open('/tmp/spike_stdout.log') if l.strip()]"
```

Negative-case proof (mandatory, executed then reverted): adding
`print("banner")` at module load pushed a `banner` line into the captured
stdout and the purity command failed with
`json.decoder.JSONDecodeError: Expecting value: line 1 column 1`. The gate
detects pollution.

**Critical corollary:** in that negative run the *round-trip still succeeded*
(`ROUNDTRIP OK`, exit 0) — the SDK client logged `Failed to parse JSONRPC
message from server` to stderr and simply skipped the bad line. **A green
round-trip is NOT evidence of a clean stdout.** The JSON-lines assertion is the
only real gate; keep it in the CI smoke test.

`uv run` itself was verified stdout-clean (its progress output goes to stderr),
so `uv run odoo-assistant` is a safe launch shape.

## 5. OpenTelemetry — on by default, currently silent, one real risk

The SDK does instrument every inbound message: `mcp/server/_otel.py`
(`OpenTelemetryMiddleware`) and `mcp/shared/_otel.py` are wired into the JSON-RPC
dispatcher, and `opentelemetry-api` is a hard dependency. **No banner and no
output was observed** because only `opentelemetry-api` is installed: the global
provider is a no-op `ProxyTracerProvider` with no exporter.

Risk for T7/T18: if a *host* environment also has `opentelemetry-sdk` installed
and configured with a **console** exporter (e.g. `OTEL_TRACES_EXPORTER=console`),
spans are written to **stdout** and would corrupt the JSON-RPC stream. Mitigation
to consider in `main()` and to re-check in the packaging/CI smoke test — the
purity assertion in §4 is what would catch it.

## 6. Reproduction commands

```bash
# API gate
uv run python -c "from mcp.server import MCPServer; s=MCPServer('test'); assert hasattr(s,'resource') and hasattr(s,'tool') and hasattr(s,'run'); print('OK')"

# Round-trip via the SDK client (also usable as a CI smoke test)
uv run python tests/spike_client.py
uv run python tests/spike_client.py --tee /tmp/spike_stdout.log
uv run python -c "import json; [json.loads(l) for l in open('/tmp/spike_stdout.log') if l.strip()]"

# Round-trip via the official inspector (options must come AFTER the target)
npx -y @modelcontextprotocol/inspector --cli .venv/bin/python tests/spike_server.py --method tools/list --format json
npx -y @modelcontextprotocol/inspector --cli .venv/bin/python tests/spike_server.py --method tools/call --tool-name echo --tool-arg text=hi --format json
```

Inspector gotcha: a `sh -c '... | tee log'` target is **not** usable — the
inspector forwards its own args into the shell command (`sh: line 1:
method:initialize: command not found`) and the connection times out. Use
`tests/spike_client.py --tee` for any stdout capture.
