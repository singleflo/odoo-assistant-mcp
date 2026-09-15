"""Marketplace manifests for Claude Code and Codex must stay installable and secret-free.

The version strings in pyproject.toml, plugins/odoo-assistant/plugin.json,
plugins/odoo-assistant/.claude-plugin/plugin.json and the Claude marketplace
entry must move together (todo 15 bumps them all at once).
"""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MANIFESTS = [
    ROOT / "plugins/odoo-assistant/plugin.json",
    ROOT / "plugins/odoo-assistant/mcp.json",
    ROOT / "plugins/odoo-assistant/.claude-plugin/plugin.json",
    ROOT / "plugins/odoo-assistant/.mcp.json",
    ROOT / ".claude-plugin/marketplace.json",
    ROOT / ".agents/plugins/marketplace.json",
]

ENV_VARS = ["ODOO_BASE_URL", "ODOO_API_KEY", "ODOO_DB"]


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _pyproject_version() -> str:
    with open(ROOT / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def test_every_manifest_json_parses():
    for path in MANIFESTS:
        assert _load(path), f"{path} is not valid JSON"


def test_plugin_version_matches_pyproject():
    expected = _pyproject_version()
    portable = _load(ROOT / "plugins/odoo-assistant/plugin.json")
    claude = _load(ROOT / "plugins/odoo-assistant/.claude-plugin/plugin.json")
    market = _load(ROOT / ".claude-plugin/marketplace.json")
    assert portable["version"] == expected
    assert claude["version"] == expected
    entry = next(p for p in market["plugins"] if p["name"] == "odoo-assistant")
    assert entry["version"] == expected


def test_claude_mcp_json_camelcase_key_and_uvx_command():
    # openai/codex#22105: snake_case mcp_servers is silently ignored, so the
    # camelCase spelling is the contract — assert it literally.
    body = (ROOT / "plugins/odoo-assistant/.mcp.json").read_text()
    assert '"mcpServers"' in body
    assert "mcp_servers" not in body
    server = _load(ROOT / "plugins/odoo-assistant/.mcp.json")["mcpServers"]["odoo-assistant"]
    assert server["command"] == "uvx"
    assert server["args"] == ["odoo-assistant"]
    for var in ENV_VARS:
        assert server["env"][var].startswith("${")


def test_portable_mcp_json_camelcase_key_and_uvx_command():
    body = (ROOT / "plugins/odoo-assistant/mcp.json").read_text()
    assert '"mcpServers"' in body
    assert "mcp_servers" not in body
    server = _load(ROOT / "plugins/odoo-assistant/mcp.json")["mcpServers"]["odoo-assistant"]
    assert server["type"] == "stdio"
    assert server["command"] == "uvx"
    assert server["args"] == ["odoo-assistant"]
    for var in ENV_VARS:
        assert var in server["env"]


def test_no_api_key_literal_in_marketplace_trees():
    pattern = re.compile(r"[0-9a-f]{40}")
    for tree in ["plugins", ".claude-plugin", ".agents"]:
        for path in (ROOT / tree).rglob("*"):
            if path.is_file():
                assert not pattern.search(path.read_text(errors="ignore")), (
                    f"40-hex literal in {path}"
                )


def test_marketplace_entries_reference_existing_plugin_dir():
    claude = _load(ROOT / ".claude-plugin/marketplace.json")
    entry = next(p for p in claude["plugins"] if p["name"] == "odoo-assistant")
    assert (ROOT / entry["source"]).is_dir()

    agents = _load(ROOT / ".agents/plugins/marketplace.json")
    entry = next(p for p in agents["plugins"] if p["name"] == "odoo-assistant")
    assert entry["source"]["source"] == "local"
    assert (ROOT / entry["source"]["path"]).is_dir()


def test_claude_plugin_manifest_points_at_existing_mcp_json():
    manifest = _load(ROOT / "plugins/odoo-assistant/.claude-plugin/plugin.json")
    target = ROOT / "plugins/odoo-assistant" / manifest["mcpServers"]
    assert target.is_file()


def test_openai_short_description_within_30_chars():
    manifest = _load(ROOT / "plugins/odoo-assistant/plugin.json")
    interface = manifest["extensions"]["com.openai"]["interface"]
    assert len(interface["shortDescription"]) <= 30
