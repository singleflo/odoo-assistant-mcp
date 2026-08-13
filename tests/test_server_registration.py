"""The consolidation gate: every Wave-3 module is actually on the real server.

Each tool module already proves its own `register()` against a throwaway
`MCPServer`. That is not the same claim: a module can register perfectly and
still be absent from `server.mcp`, which is the only instance a host ever
talks to. These tests assert on that singleton.
"""
import anyio
import pytest

from odoo_assistant import server

EXPECTED_TOOLS = {
    "search_read", "read_record", "count_records", "instance_overview",
    "create_record", "write_record", "run_action", "cancel_record",
    "notify_user", "create_activity", "download_docs", "generate_pdf",
    "explore_module", "list_known_modules",
}


@pytest.fixture(scope="module", autouse=True)
def wired_server():
    """Wire the singleton once, exactly as `main()` does before `mcp.run()`."""
    server._register_all()


def test_every_tool_module_is_on_the_server():
    """Given the wired server, When its tools are listed, Then all 14 are there."""
    listed = {tool.name for tool in anyio.run(server.mcp.list_tools)}

    assert listed == EXPECTED_TOOLS
    assert len(listed) == 14


def test_the_reference_resources_are_on_the_server():
    """Given the wired server, When resources are listed, Then odoo:// is served."""
    listed = {str(resource.uri) for resource in anyio.run(server.mcp.list_resources)}

    assert "odoo://skill" in listed
    assert any(uri.startswith("odoo://ref/") for uri in listed)
