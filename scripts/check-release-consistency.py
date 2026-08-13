#!/usr/bin/env python3
"""Check that server.json, pyproject.toml and README.md agree before a release.

Two claims are verified:
  1. server.json "version" == pyproject.toml [project] version
  2. README.md "<!-- mcp-name: ... -->" == server.json "name"

Exits 0 when both hold, 1 otherwise, printing MATCH/MISMATCH per claim.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MCP_NAME_COMMENT = re.compile(r"<!-- mcp-name: (?P<name>[^\s>]+) -->")


def readme_mcp_name(readme: str) -> str | None:
    found = MCP_NAME_COMMENT.search(readme)
    return found.group("name") if found else None


def report(claim: str, expected: str, actual: str | None) -> bool:
    ok = expected == actual
    verdict = "MATCH" if ok else "MISMATCH"
    print(f"{verdict}: {claim} (server.json={expected!r}, other={actual!r})")
    return ok


def main() -> int:
    manifest = json.loads((ROOT / "server.json").read_text())
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    readme = (ROOT / "README.md").read_text()

    version_ok = report("version", manifest["version"], pyproject["project"]["version"])
    name_ok = report("mcp-name", manifest["name"], readme_mcp_name(readme))
    return 0 if version_ok and name_ok else 1


if __name__ == "__main__":
    sys.exit(main())
