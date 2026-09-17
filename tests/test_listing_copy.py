"""The listing dossier cannot drift: sizes, sections and live annotations.

docs/listing/README.md is the file a human pastes from at submission time.
The stores enforce hard limits (Claude: tagline 55, description 2000; OpenAI:
prompts 128, display name 30, short description 30) and require the
annotation values to match each tool's real behaviour. These tests parse the
dossier and fail on a size violation, a missing section, or an annotation
table row that no longer equals what `list_tools()` returns on the wire.
"""

from __future__ import annotations

import re
from pathlib import Path

import anyio
import pytest

from odoo_assistant import server

LISTING = Path(__file__).resolve().parent.parent / "docs" / "listing" / "README.md"

API_KEY_PATTERN = re.compile(r"\b[0-9a-f]{40}\b")
CASE_LABELS = ("Prompt:", "Expected tool:", "Expected result:", "Fixture data:")


def _blocks_by_heading(text: str) -> dict[str, list[str]]:
    """Every fenced ``` block, attributed to the nearest preceding heading."""
    blocks: dict[str, list[str]] = {}
    heading = ""
    inside = False
    current: list[str] = []
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


def _first(blocks: dict[str, list[str]], heading: str) -> str:
    assert heading in blocks, f"no fenced block under the {heading!r} heading"
    return blocks[heading][0]


def _sections(text: str, pattern: str) -> list[str]:
    """The text between each heading matching `pattern` and the next line."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if re.match(pattern, line)]
    return [
        "\n".join(lines[start:stop])
        for start, stop in zip(starts, [*starts[1:], len(lines)])
    ]


def _check_copy(text: str) -> None:
    """Every paste-ready field exists and fits the store's published limit."""
    blocks = _blocks_by_heading(text)

    name = _first(blocks, "Name")
    assert 0 < len(name) <= 100, f"name is {len(name)} chars, limit 100"
    plugin_name = _first(blocks, "Plugin name")
    assert 0 < len(plugin_name) <= 64, f"plugin name is {len(plugin_name)} chars, limit 64"
    display_name = _first(blocks, "Display name")
    assert 0 < len(display_name) <= 30, f"display name is {len(display_name)} chars, limit 30"
    tagline = _first(blocks, "Tagline")
    assert 0 < len(tagline) <= 55, (
        f"tagline is {len(tagline)} chars, limit 55: {tagline!r}")
    short_description = _first(blocks, "Short description")
    assert 0 < len(short_description) <= 30, (
        f"short description is {len(short_description)} chars, limit 30: "
        f"{short_description!r}")
    long_description = _first(blocks, "Long description")
    assert 0 < len(long_description) <= 2000, (
        f"long description is {len(long_description)} chars, limit 2000")


@pytest.fixture(scope="module", autouse=True)
def wired_server():
    """Wire the singleton once, exactly as `main()` does before `mcp.run()`."""
    server._register_all()


def test_paste_ready_copy_respects_the_store_limits():
    _check_copy(LISTING.read_text(encoding="utf-8"))


def test_the_checker_catches_a_drifted_tagline():
    """The QA probe: a 60-char tagline must fail, not pass silently."""
    text = LISTING.read_text(encoding="utf-8")
    drifted = text.replace(_blocks_by_heading(text)["Tagline"][0], "x" * 60, 1)
    with pytest.raises(AssertionError, match="tagline is 60 chars"):
        _check_copy(drifted)


def test_exactly_three_starter_prompts_within_128_chars():
    blocks = _blocks_by_heading(LISTING.read_text(encoding="utf-8"))
    prompts = blocks.get("Starter prompts", [])
    assert len(prompts) == 3, f"expected exactly 3 starter prompts, found {len(prompts)}"
    for prompt in prompts:
        assert 0 < len(prompt) <= 128, f"prompt is {len(prompt)} chars: {prompt!r}"
        assert "@" not in prompt, f"starter prompt contains an @mention: {prompt!r}"


def test_five_positive_and_three_negative_cases_with_all_fields():
    text = LISTING.read_text(encoding="utf-8")
    positives = _sections(text, r"^#+ Positive test case \d")
    negatives = _sections(text, r"^#+ Negative test case \d")
    assert len(positives) == 5, f"expected 5 positive test cases, found {len(positives)}"
    assert len(negatives) == 3, f"expected 3 negative test cases, found {len(negatives)}"
    for case in positives + negatives:
        for label in CASE_LABELS:
            assert label in case, f"test case missing {label!r}:\n{case[:300]}"


def test_annotation_table_matches_the_live_wire_annotations():
    text = LISTING.read_text(encoding="utf-8")
    section = _sections(text, r"^##+ Tool annotations")[0]
    rows = re.findall(
        r"^\| `([a-z_]+)` \| (yes|no) \| (yes|no) \| (yes|no) \|",
        section, re.MULTILINE)
    table = {
        name: (ro == "yes", destructive == "yes", open_world == "yes")
        for name, ro, destructive, open_world in rows
    }
    live = {}
    for tool in anyio.run(server.mcp.list_tools):
        assert tool.annotations is not None, tool.name
        live[tool.name] = (
            tool.annotations.read_only_hint,
            tool.annotations.destructive_hint,
            tool.annotations.open_world_hint)
    assert len(live) == 22
    assert set(table) == set(live), "table rows and live tools disagree on names"
    assert table == live, (
        "the dossier's annotation table has drifted from the live annotations:\n"
        + "\n".join(
            f"  {name}: table={table.get(name)} live={live[name]}"
            for name in sorted(live) if table.get(name) != live[name]))


def test_urls_sections_and_no_real_credentials():
    text = LISTING.read_text(encoding="utf-8")
    blocks = _blocks_by_heading(text)
    assert _first(blocks, "Privacy URL") == "https://mcp.singleflo.com/privacy"
    assert _first(blocks, "Terms URL") == "https://mcp.singleflo.com/terms"
    assert _first(blocks, "Documentation URL").startswith(
        "https://github.com/singleflo/odoo-assistant-mcp")
    assert _first(blocks, "Support URL").startswith(
        "https://github.com/singleflo/odoo-assistant-mcp")
    assert "worldwide" in _first(blocks, "Country availability").lower()
    assert "Reviewer test account" in text, "the reviewer-account template is missing"
    assert "screenshots are not required" in text.lower()
    assert not API_KEY_PATTERN.search(text), (
        "a 40-hex string that looks like a real API key sits in the dossier")
