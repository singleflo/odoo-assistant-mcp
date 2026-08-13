"""Scripted MCP client round-trip against tests/spike_server.py (plan todo 3).

Spawns the probe server over stdio, runs tools/list then tools/call, and asserts
the results. With --tee PATH the server is launched behind `tee` so its raw
stdout is captured while the very same round-trip runs (stdout-purity gate).

    uv run python tests/spike_client.py
    uv run python tests/spike_client.py --tee /tmp/spike_stdout.log

Client stdout is not the server's stdout, so printing here is safe.
"""

import shlex
import sys

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SERVER_SCRIPT = "tests/spike_server.py"


def server_params(tee_path: str | None) -> StdioServerParameters:
    """Launch the probe server directly, or behind `tee` when capturing stdout."""
    direct = f"{shlex.quote(sys.executable)} {shlex.quote(SERVER_SCRIPT)}"
    if tee_path is None:
        return StdioServerParameters(command=sys.executable, args=[SERVER_SCRIPT])
    return StdioServerParameters(
        command="sh", args=["-c", f"{direct} | tee {shlex.quote(tee_path)}"]
    )


async def roundtrip(tee_path: str | None) -> None:
    async with stdio_client(server_params(tee_path)) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            assert names == ["echo"], f"tools/list returned {names}"

            result = await session.call_tool("echo", {"text": "hi"})
            text = result.content[0].text
            assert text == "hi", f"tools/call returned {text!r}"

    print(f"ROUNDTRIP OK protocol={init.protocol_version} tools={names} echo={text!r}")


if __name__ == "__main__":
    tee = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--tee" else None
    anyio.run(roundtrip, tee)
