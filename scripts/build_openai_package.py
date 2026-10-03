#!/usr/bin/env python3
"""Build the ZIP the OpenAI plugin portal takes, from the listing dossier.

docs/listing/README.md is the single source of the listing copy. This script
renders it into docs/listing/openai/ — the Codex-format manifest
`.codex-plugin/plugin.json` and the MCP declaration `.mcp.json` — and zips
that directory, icons included, into docs/listing/singleflo-for-odoo.zip.

The MCP declaration is not optional: the portal attaches a server only from
the package uploaded when the plugin is created, and a plugin created without
one can never gain one. Review test cases and the demo video travel in the
package too; reviewer credentials never do — they go into the portal's
Review details form.

    uv run python scripts/build_openai_package.py          # write + zip
    uv run python scripts/build_openai_package.py --check  # fail on drift
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LISTING = REPO / "docs" / "listing" / "README.md"
PACKAGE = REPO / "docs" / "listing" / "openai"
MANIFEST = PACKAGE / ".codex-plugin" / "plugin.json"
MCP_CONFIG = PACKAGE / ".mcp.json"
ZIP_PATH = REPO / "docs" / "listing" / "singleflo-for-odoo.zip"

MCP_SERVER_NAME = "singleflo-for-odoo"
MCP_URL = "https://mcp.singleflo.com/mcp"
WEBSITE_URL = "https://singleflo.com/en/odoo-assistant"
SUPPORT_URL = "https://singleflo.com/en/odoo-assistant#support"
DEMO_URL = (
    "https://github.com/singleflo/odoo-assistant-mcp/blob/main/"
    "docs/demo/odoo-assistant-demo.mov")
PUBLISHER = "Persevida SL"
CATEGORY = "Business & Operations"


def blocks_by_heading(text: str) -> dict[str, list[str]]:
    """Every fenced ``` block, attributed to the nearest preceding heading."""
    blocks: dict[str, list[str]] = {}
    heading, inside, current = "", False, []
    for line in text.splitlines():
        if line.startswith("```"):
            if inside:
                blocks.setdefault(heading, []).append("\n".join(current).strip())
                current = []
            inside = not inside
            continue
        if inside:
            current.append(line)
        elif line.startswith("#"):
            heading = line.lstrip("#").strip()
    return blocks


def _cases(text: str, kind: str) -> list[dict[str, str]]:
    """The dossier's `### <kind> test case N: <title>` sections as fields."""
    pattern = re.compile(
        rf"^### {kind} test case \d+: (.+?)\n(.*?)(?=^#|\Z)", re.M | re.S)
    cases = []
    for title, body in pattern.findall(text):
        fields: dict[str, str] = {}
        for label, value in re.findall(
                r"^- ([A-Z][\w ]*?): (.*?)(?=^- [A-Z]|\Z)", body, re.M | re.S):
            fields[label] = " ".join(value.split())
        fields["title"] = title.strip()
        cases.append(fields)
    return cases


def _tool_names(text: str) -> set[str]:
    return set(re.findall(r"^\| `([a-z_]+)` \| (?:yes|no) \|", text, re.M))


def render(text: str) -> tuple[dict, dict]:
    """The manifest and MCP declaration the dossier describes."""
    blocks = blocks_by_heading(text)
    first = lambda heading: blocks[heading][0]  # noqa: E731
    version_match = re.search(
        r'^version = "([^"]+)"', (REPO / "pyproject.toml").read_text(), re.M)
    assert version_match, "pyproject.toml carries no version"
    version = version_match[1]
    tools = _tool_names(text)

    def sentence(value: str) -> str:
        return value[0].upper() + value[1:]

    positive = []
    for case in _cases(text, "Positive"):
        triggered = [t for t in re.findall(r"`([a-z_]+)`", case["Expected tool"])
                     if t in tools]
        positive.append({
            "description": sentence(case["title"]),
            "prompt": case["Prompt"],
            "tools_triggered": ", ".join(dict.fromkeys(triggered)),
            "expected_behavior": sentence(case["Expected result"]),
        })
    negative = [{
        "description": sentence(case["title"]) + ". " + sentence(case["Why not"]),
        "prompt": case["Prompt"],
    } for case in _cases(text, "Negative")]

    description = first("Long description")
    manifest = {
        "name": first("Plugin name"),
        "version": version,
        "description": description,
        "author": {"name": PUBLISHER},
        "mcpServers": "./.mcp.json",
        "interface": {
            "displayName": first("Display name"),
            "shortDescription": first("Short description"),
            "longDescription": description,
            "developerName": PUBLISHER,
            "category": CATEGORY,
            "capabilities": first("Capabilities").splitlines(),
            "websiteURL": WEBSITE_URL,
            "supportURL": SUPPORT_URL,
            "privacyPolicyURL": first("Privacy URL"),
            "termsOfServiceURL": first("Terms URL"),
            "defaultPrompt": blocks["Starter prompts"],
            "composerIcon": "./assets/icon.png",
            "logo": "./assets/logo.png",
        },
        "extensions": {
            "com.openai": {
                "review": {
                    "test_cases": {"positive": positive, "negative": negative},
                    "demo_recording_url": DEMO_URL,
                    "commerce": False,
                    "commerce_description":
                        "This plugin does not sell products or process payments.",
                },
                "publication": {
                    # The dossier says worldwide; [] removes every restriction.
                    "countries": [],
                    "release_notes": first("Release notes"),
                },
            },
        },
    }
    mcp = {"mcpServers": {MCP_SERVER_NAME: {"url": MCP_URL}}}
    return manifest, mcp


def _dump(obj: dict) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    manifest, mcp = render(LISTING.read_text(encoding="utf-8"))
    wanted = {MANIFEST: _dump(manifest), MCP_CONFIG: _dump(mcp)}
    if "--check" in sys.argv[1:]:
        stale = [str(p.relative_to(REPO)) for p, body in wanted.items()
                 if not p.is_file() or p.read_text(encoding="utf-8") != body]
        if stale:
            print("stale, run scripts/build_openai_package.py:", *stale)
            return 1
        return 0
    for path, body in wanted.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    members = [MANIFEST, MCP_CONFIG, *sorted((PACKAGE / "assets").glob("*.png"))]
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as archive:
        for member in members:
            archive.write(member, member.relative_to(PACKAGE).as_posix())
    print(f"wrote {ZIP_PATH.relative_to(REPO)}:",
          ", ".join(m.relative_to(PACKAGE).as_posix() for m in members))
    return 0


if __name__ == "__main__":
    sys.exit(main())
