"""Throwaway MCP probe server used by the SDK-verify spike (plan todo 3).

Kept as executable documentation of the verified mcp 2.x server shape.
Findings live in tests/SPIKE_NOTES.md. Not collected by pytest (no test_ prefix).

Run: uv run python tests/spike_server.py   (speaks JSON-RPC on stdio)
"""

from mcp.server import MCPServer

mcp = MCPServer("spike")


@mcp.tool()
def echo(text: str) -> str:
    """Echo back the given text.

    Args:
        text: The text to echo.
    """
    return text


if __name__ == "__main__":
    mcp.run(transport="stdio")
