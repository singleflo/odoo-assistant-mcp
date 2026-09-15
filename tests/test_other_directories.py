import os
import re
import pytest

DOC_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "listing", "OTHER-DIRECTORIES.md")

REQUIRED_FIELDS = [
    "URL",
    "What to paste",
    "Transport",
    "Review required",
    "Cost",
]

def test_other_directories_file_exists():
    assert os.path.exists(DOC_PATH), f"File {DOC_PATH} does not exist"

def test_other_directories_no_tbd():
    assert os.path.exists(DOC_PATH)
    with open(DOC_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "TBD" not in content, "Found TBD placeholder in OTHER-DIRECTORIES.md"

def test_other_directories_sections_and_fields():
    assert os.path.exists(DOC_PATH)
    with open(DOC_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Find sections under ## Confirmed directories
    in_confirmed = False
    sections = []
    current_section = None
    current_fields = set()

    for line in lines:
        line_str = line.strip()
        if line_str == "## Confirmed directories":
            in_confirmed = True
            continue
        elif line_str.startswith("## ") and in_confirmed:
            # Reached next top-level section e.g. ## Dropped directories
            break

        if in_confirmed and line_str.startswith("### "):
            if current_section:
                sections.append((current_section, current_fields))
            current_section = line_str[4:].strip()
            current_fields = set()
        elif in_confirmed and current_section and line_str.startswith("- **"):
            match = re.match(r"-\s*\*\*([^*]+)\*\*:", line_str)
            if match:
                field_name = match.group(1).strip()
                current_fields.add(field_name)

    if current_section:
        sections.append((current_section, current_fields))

    assert len(sections) > 0, "No confirmed catalog sections found under '## Confirmed directories'"

    for sec_name, fields in sections:
        for req_field in REQUIRED_FIELDS:
            assert req_field in fields, f"Section '{sec_name}' missing field '{req_field}' (found: {fields})"
