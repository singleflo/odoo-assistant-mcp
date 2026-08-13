"""The installed-artifact suite: a real MCP session against the BUILT WHEEL.

`tests/test_protocol.py` proves the protocol against the dev checkout (`uv run
odoo-assistant`). That is not the thing a user installs. This module spawns the
console script out of `dist/*.whl` through `uvx`, exactly as the README's host
configs do, and drives `initialize` -> `tools/call` over real stdio.

It exists because the smoke check it replaces proved nothing. `printf '' | uvx
--from dist/*.whl odoo-assistant` exits 0 because EOF on stdin closes the
session BEFORE any tool runs, so `_credentials()` is never reached and a
missing-credentials build would have passed. Same class of trap as T3's: a
clean exit is not evidence that the code path you care about executed. Only an
actual `tools/call` forces credential resolution, and only its response proves
the failure reached the client.

No Odoo instance is involved: the credential variables are blanked, which is
what makes `search_read` fail — and that failure is the vehicle.

Marked `wheel` and excluded from the default run: it needs `uv build` output on
disk and network for `uvx` to resolve dependencies. CI builds the wheel already,
so it runs there (`.github/workflows/tests.yml`).
"""

import json
import shlex
from pathlib import Path
from typing import NamedTuple

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult, TextContent

pytestmark = pytest.mark.wheel

DIST_DIR = Path(__file__).resolve().parent.parent / "dist"

# Blanked rather than absent, mirroring test_protocol.py: `_credentials()`
# refuses an empty value too, so no inherited variable can reach an instance.
NO_CREDENTIALS = {
    name: "" for name in ("ODOO_BASE_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY")
}
A_KEY_IS_SET = {**NO_CREDENTIALS, "ODOO_API_KEY": "dummy-key-never-sent-anywhere"}

# The anchor the blame list is parsed from — see `_blamed()`.
BLAME_PREFIX = "Missing Odoo credentials: "

# initialize + tools/call.
EXPECTED_RESPONSES = 2
SESSION_TIMEOUT_SECONDS = 180


class WheelSession(NamedTuple):
    """One session against the artifact: the parsed result and raw stdout."""

    call: CallToolResult
    stdout_lines: list[str]


@pytest.fixture(scope="module")
def wheel() -> Path:
    """The artifact under test. Absent means the operator skipped `uv build`."""
    built = sorted(DIST_DIR.glob("odoo_assistant-*.whl"), key=lambda p: p.stat().st_mtime)
    if not built:
        pytest.fail(f"No wheel in {DIST_DIR}. Run `uv build` — this suite tests the artifact.")
    return built[-1]


def _server(wheel: Path, capture: Path, env: dict[str, str]) -> StdioServerParameters:
    """Launch the wheel's console script behind `tee`, so raw stdout is kept.

    `--refresh-package` is load-bearing, not caution: uvx keys its cached
    environment on name==version, so a rebuilt `1.0.0` wheel is silently
    ignored and the session runs the PREVIOUS build. Observed, not theorised —
    without it this session reported the `ODOO_PASSWORD` wording that commit
    81672f5 had already deleted from the source.
    """
    launch = f"uvx --refresh-package odoo-assistant --from {shlex.quote(str(wheel))} odoo-assistant"
    return StdioServerParameters(
        command="sh",
        args=["-c", f"{launch} | tee {shlex.quote(str(capture))}"],
        env=env,
    )


async def _call_search_read(wheel: Path, workdir: Path, env: dict[str, str]) -> WheelSession:
    """Drive the one call that forces the installed server to resolve credentials."""
    capture = workdir / "stdout.jsonl"
    with (workdir / "stderr.log").open("w", encoding="utf-8") as stderr:
        with anyio.fail_after(SESSION_TIMEOUT_SECONDS):
            async with stdio_client(_server(wheel, capture, env), errlog=stderr) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    called = await session.call_tool(
                        "search_read",
                        {"model": "res.partner", "domain": [["id", "=", 1]]},
                    )

    return WheelSession(
        call=called,
        stdout_lines=[
            line for line in capture.read_text(encoding="utf-8").splitlines() if line.strip()
        ],
    )


@pytest.fixture(scope="module")
def uncredentialed(wheel: Path, tmp_path_factory) -> WheelSession:
    """The claim under test: a tool call with nothing configured."""
    return anyio.run(_call_search_read, wheel, tmp_path_factory.mktemp("wheel-none"), NO_CREDENTIALS)


@pytest.fixture(scope="module")
def with_a_key(wheel: Path, tmp_path_factory) -> WheelSession:
    """The control: same call, but the key IS supplied."""
    return anyio.run(_call_search_read, wheel, tmp_path_factory.mktemp("wheel-key"), A_KEY_IS_SET)


def _text(session: WheelSession) -> str:
    block = session.call.content[0]
    assert isinstance(block, TextContent)
    return block.text


def _blamed(text: str) -> set[str]:
    """The variables the installed `_credentials()` itself computed as missing.

    Parsed, never substring-matched: the sentence AFTER the list names
    ODOO_API_KEY unconditionally, so `"ODOO_API_KEY" in text` holds even when
    the key was supplied. Only this list is derived from the environment, so
    only this list can tell the two cases apart.
    """
    _, _, tail = text.partition(BLAME_PREFIX)
    listed, _, _ = tail.partition(".")
    return {name for name in listed.split(", ") if name}


def test_the_installed_wheel_reaches_the_credential_check(uncredentialed: WheelSession):
    """Given the built wheel, When a tool is called uncredentialed, Then it is blamed."""
    assert uncredentialed.call.is_error is True
    assert _blamed(_text(uncredentialed)) == set(NO_CREDENTIALS)


def test_supplying_the_key_takes_it_off_the_blame_list(with_a_key: WheelSession):
    """Given a key IS set, When the same call runs, Then only the rest are blamed.

    This is what makes the test above load-bearing. Both sessions fail and both
    texts contain the literal `ODOO_API_KEY`, so the naive assertion cannot
    distinguish them; the blame list can, and does.
    """
    text = _text(with_a_key)

    assert "ODOO_API_KEY" in text
    assert _blamed(text) == set(NO_CREDENTIALS) - {"ODOO_API_KEY"}


def test_that_failure_reaches_the_wire_as_isError_from_the_artifact(uncredentialed: WheelSession):
    """Given the session, When stdout is read, Then isError:true is literally on it.

    The client-side assertion could hold while the SDK encoded the failure as a
    JSON-RPC `error` envelope, which a model never sees as tool output. This
    reads the bytes the installed server actually wrote — and parsing every one
    of them as JSON is also the stdout-purity gate for the artifact.
    """
    messages = [json.loads(line) for line in uncredentialed.stdout_lines]
    tool_results = [
        message["result"] for message in messages if "content" in message.get("result", {})
    ]

    assert len(messages) == EXPECTED_RESPONSES
    assert {message["jsonrpc"] for message in messages} == {"2.0"}
    assert [message for message in messages if "error" in message] == []
    assert len(tool_results) == 1
    assert tool_results[0]["isError"] is True
