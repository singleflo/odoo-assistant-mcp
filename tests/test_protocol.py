"""The protocol suite: the REAL server, spoken to over real stdio JSON-RPC.

Every other test in this repo talks to `server.mcp` in-process. That proves
registration, not the protocol — a host never imports us, it spawns the
`odoo-assistant` console script and reads JSON-RPC off its stdout. This module
is the only one that makes that claim, and it makes it against the production
entry point declared in pyproject.toml.

Two things are proven on ONE captured session, because they are the same run:
what the client parsed, and what was literally on the wire. T3's negative proof
(`tests/SPIKE_NOTES.md` §4) is why the second half exists — with a stray
`print()` the round-trip still succeeds, since the SDK client logs the junk
line to stderr and skips it. **Only parsing the raw capture as JSON lines
detects stdout pollution**, so the server runs behind `tee` and every line of
that file is parsed here.

No Odoo instance is involved. The server is spawned with the credential
variables blanked, which is precisely what makes `search_read` fail — and that
failure is the vehicle for proving an exception travels all the way to
`isError: true` on the wire.
"""

import json
import shlex
from pathlib import Path
from typing import NamedTuple

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import (
    CallToolResult,
    ReadResourceResult,
    TextContent,
    TextResourceContents,
)

from tests.test_server_registration import EXPECTED_TOOLS

REPO_ROOT = Path(__file__).resolve().parent.parent

# The production launch shape: the console script, through uv, exactly as a
# host config would. `uv` itself is stdout-clean (SPIKE_NOTES §4).
LAUNCH = "uv run odoo-assistant"

# Blanked rather than merely absent. `get_default_environment()` already drops
# every ODOO_* variable, but an explicit empty value is refused by
# `_credentials()` too, so this suite cannot reach a live instance even if the
# SDK ever inherits more of the environment.
NO_CREDENTIALS = {
    name: "" for name in ("ODOO_BASE_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY")
}

# One resource per file in `references_public/`. Membership, not a total: a dev
# machine may also serve references `explore_module` generated locally.
BUNDLED_REFERENCES = {
    "odoo://ref/SKILL",
    "odoo://ref/collaboration",
    "odoo://ref/deletion",
    "odoo://ref/documents",
    "odoo://ref/payments",
    "odoo://ref/recipes",
    "odoo://ref/writing",
}

# initialize + tools/list + tools/call + resources/list + resources/read.
EXPECTED_RESPONSES = 5
ROUNDTRIP_TIMEOUT_SECONDS = 120


class Roundtrip(NamedTuple):
    """One captured session: what the client got, and what stdout carried."""

    tools: set[str]
    uncredentialed_call: CallToolResult
    resources: set[str]
    skill: ReadResourceResult
    stdout_lines: list[str]


def _server(capture: Path) -> StdioServerParameters:
    """Launch the console script behind `tee`, so raw stdout is kept."""
    return StdioServerParameters(
        command="sh",
        args=["-c", f"{LAUNCH} | tee {shlex.quote(str(capture))}"],
        env=NO_CREDENTIALS,
        cwd=REPO_ROOT,
    )


async def _roundtrip(capture: Path, errlog: Path) -> Roundtrip:
    """Drive the four protocol methods the acceptance criteria name."""
    with errlog.open("w", encoding="utf-8") as stderr:
        with anyio.fail_after(ROUNDTRIP_TIMEOUT_SECONDS):
            async with stdio_client(_server(capture), errlog=stderr) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    called = await session.call_tool(
                        "search_read",
                        {"model": "res.partner", "domain": [["id", "=", 1]]},
                    )
                    served = await session.list_resources()
                    skill = await session.read_resource("odoo://skill")

    return Roundtrip(
        tools={tool.name for tool in listed.tools},
        uncredentialed_call=called,
        resources={str(resource.uri) for resource in served.resources},
        skill=skill,
        stdout_lines=[
            line for line in capture.read_text(encoding="utf-8").splitlines() if line.strip()
        ],
    )


@pytest.fixture(scope="module")
def roundtrip(tmp_path_factory) -> Roundtrip:
    """Spawn the real server once; every test below reads that one session."""
    workdir = tmp_path_factory.mktemp("protocol")
    return anyio.run(_roundtrip, workdir / "stdout.jsonl", workdir / "stderr.log")


def _wire_messages(roundtrip: Roundtrip) -> list[dict]:
    """Every stdout line as JSON. THE stdout-purity gate — it raises on junk."""
    return [json.loads(line) for line in roundtrip.stdout_lines]


def test_tools_list_serves_exactly_the_registered_tools(roundtrip: Roundtrip):
    """Given the real server, When tools are listed, Then all 14 are offered."""
    assert roundtrip.tools == EXPECTED_TOOLS
    assert len(roundtrip.tools) == 14


def test_a_tool_without_credentials_reports_isError_to_the_client(roundtrip: Roundtrip):
    """Given no credentials, When search_read runs, Then the result is an error."""
    result = roundtrip.uncredentialed_call
    block = result.content[0]

    assert result.is_error is True
    assert isinstance(block, TextContent)
    assert "ODOO_BASE_URL" in block.text
    assert "nothing was sent to Odoo" in block.text
    assert "may or may not have been applied" not in block.text


def test_that_error_reaches_the_wire_as_isError_and_not_a_protocol_code(roundtrip: Roundtrip):
    """Given that failed call, When stdout is read, Then isError:true is on it.

    The Python-level assertion above could hold while the SDK encoded the
    failure as a JSON-RPC `error` envelope with a numeric code — which the
    model never sees as tool output. This reads the bytes instead.
    """
    messages = _wire_messages(roundtrip)
    tool_results = [
        message["result"] for message in messages if "content" in message.get("result", {})
    ]

    assert len(tool_results) == 1
    assert tool_results[0]["isError"] is True
    assert [message for message in messages if "error" in message] == []


def test_resources_list_serves_the_skill_and_the_reference_bundle(roundtrip: Roundtrip):
    """Given the real server, When resources are listed, Then odoo:// is served."""
    assert "odoo://skill" in roundtrip.resources
    assert BUNDLED_REFERENCES <= roundtrip.resources
    assert len([uri for uri in roundtrip.resources if uri.startswith("odoo://ref/")]) >= 7


def test_reading_the_skill_resource_returns_markdown(roundtrip: Roundtrip):
    """Given odoo://skill, When it is read, Then markdown text comes back."""
    contents = roundtrip.skill.contents[0]

    assert isinstance(contents, TextResourceContents)
    assert contents.mime_type == "text/markdown"
    assert any(line.startswith("# ") for line in contents.text.splitlines())


def test_stdout_carries_json_rpc_and_nothing_else(roundtrip: Roundtrip):
    """Given the captured stdout, When every line is parsed, Then all are JSON-RPC.

    A green round-trip is NOT evidence of a clean stdout: the client skips a
    line it cannot parse and logs to stderr. This is the assertion that bites.
    """
    messages = _wire_messages(roundtrip)

    assert len(messages) == EXPECTED_RESPONSES
    assert {message["jsonrpc"] for message in messages} == {"2.0"}
