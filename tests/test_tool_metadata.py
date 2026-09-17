"""Store-grade metadata on the real wire surface: title + ToolAnnotations.

Claude's directory requires a title plus the applicable readOnlyHint or
destructiveHint on every tool; OpenAI requires readOnlyHint, openWorldHint
and destructiveHint. Both are submission gates. These assertions run against
`server.mcp.list_tools()` — what a host actually receives — rather than the
registration objects, so a title the SDK dropped still fails here.
"""
import anyio
import pytest

from odoo_assistant import server
from tests.test_server_registration import EXPECTED_TOOLS


@pytest.fixture(scope="module", autouse=True)
def wired_server():
    """Wire the singleton once, exactly as `main()` does before `mcp.run()`."""
    server._register_all()


def test_every_tool_carries_a_title_and_store_grade_annotations():
    """Given the wired server, When tools are listed over the wire,
    Then each of the 21 carries a non-empty title and annotations that
    satisfy both store checklists, and no tool was lost or renamed."""
    tools = anyio.run(server.mcp.list_tools)

    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert isinstance(tool.title, str) and tool.title, tool.name
        assert tool.annotations is not None, tool.name
        assert (
            tool.annotations.read_only_hint is True
            or tool.annotations.destructive_hint is not None
        ), tool.name
        assert tool.annotations.open_world_hint is not None, tool.name
