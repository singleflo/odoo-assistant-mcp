import re
import urllib.request
import urllib.error
from pathlib import Path
import pytest

DOSSIER_PATH = Path("docs/listing/README.md")
SUBMIT_CLAUDE_PATH = Path("docs/listing/SUBMIT-CLAUDE.md")
SUBMIT_OPENAI_PATH = Path("docs/listing/SUBMIT-OPENAI.md")


def test_submit_guides_exist():
    assert SUBMIT_CLAUDE_PATH.is_file(), f"Missing {SUBMIT_CLAUDE_PATH}"
    assert SUBMIT_OPENAI_PATH.is_file(), f"Missing {SUBMIT_OPENAI_PATH}"


def test_dossier_section_coverage():
    dossier_text = DOSSIER_PATH.read_text(encoding="utf-8")
    h2_sections = re.findall(r"^##\s+(.+)$", dossier_text, re.MULTILINE)
    assert h2_sections, "No ## sections found in dossier"

    claude_text = SUBMIT_CLAUDE_PATH.read_text(encoding="utf-8") if SUBMIT_CLAUDE_PATH.is_file() else ""
    openai_text = SUBMIT_OPENAI_PATH.read_text(encoding="utf-8") if SUBMIT_OPENAI_PATH.is_file() else ""
    combined_guides = claude_text + "\n" + openai_text

    unreferenced = []
    for section in h2_sections:
        # Match exact section name (case-insensitive) in at least one guide
        if not re.search(re.escape(section), combined_guides, re.IGNORECASE):
            unreferenced.append(section)

    assert not unreferenced, f"The following dossier sections are not referenced in any guide: {unreferenced}"


def test_guides_urls_liveness():
    claude_text = SUBMIT_CLAUDE_PATH.read_text(encoding="utf-8") if SUBMIT_CLAUDE_PATH.is_file() else ""
    openai_text = SUBMIT_OPENAI_PATH.read_text(encoding="utf-8") if SUBMIT_OPENAI_PATH.is_file() else ""
    combined_text = claude_text + "\n" + openai_text

    # Match URLs stopping at space, closing paren/bracket, or trailing backtick
    raw_urls = set(re.findall(r"https?://[^\s\)>\]\",`]+", combined_text))
    # Strip any trailing punct/backtick just in case
    urls = {url.rstrip("`.,;") for url in raw_urls}
    assert urls, "No URLs found in submission guides"

    failed_urls = []
    for url in urls:
        # Skip mcp.singleflo.com, singleflo.com domain URLs or portal pages that may be login-gated/403/offline
        if "singleflo.com" in url:
            continue

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Python submission guide validator)"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                code = resp.getcode()
                if code not in (200, 301, 302, 303, 307, 308):
                    failed_urls.append(f"{url} -> status {code}")
        except urllib.error.HTTPError as e:
            # 403 / 401 login-gated URLs are accepted for portals (e.g., claude.ai / platform.openai.com)
            if e.code in (403, 401):
                continue
            failed_urls.append(f"{url} -> HTTPError {e.code}")
        except Exception as e:
            failed_urls.append(f"{url} -> Exception {e}")

    assert not failed_urls, f"URL liveness check failed for: {failed_urls}"
