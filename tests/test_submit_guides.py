import re
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

DOSSIER_PATH = Path("docs/listing/README.md")
SUBMIT_CLAUDE_PATH = Path("docs/listing/SUBMIT-CLAUDE.md")
SUBMIT_OPENAI_PATH = Path("docs/listing/SUBMIT-OPENAI.md")

# Dossier sections each store asks about directly: EVERY guide must carry
# them, checked per guide — concatenating the guides would let one guide
# cover for the other and hide an unfinished submission.
PER_GUIDE_SECTIONS = ("Country availability",)

# The hosted server is not deployed yet. A DNS failure for it is the
# expected state until deploy: attempted on every run, recorded as
# EXPECTED-PENDING-DEPLOY, never silently skipped.
EXPECTED_PENDING_HOSTS = ("mcp.singleflo.com",)


def _guide_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def test_submit_guides_exist():
    assert SUBMIT_CLAUDE_PATH.is_file(), f"Missing {SUBMIT_CLAUDE_PATH}"
    assert SUBMIT_OPENAI_PATH.is_file(), f"Missing {SUBMIT_OPENAI_PATH}"


def test_dossier_section_coverage():
    dossier_text = DOSSIER_PATH.read_text(encoding="utf-8")
    h2_sections = re.findall(r"^##\s+(.+)$", dossier_text, re.MULTILINE)
    assert h2_sections, "No ## sections found in dossier"

    # Per-guide check for the sections both stores ask about.
    missing_per_guide = []
    for section in PER_GUIDE_SECTIONS:
        assert section in h2_sections, f"{section!r} is not a dossier ## section"
        for path in (SUBMIT_CLAUDE_PATH, SUBMIT_OPENAI_PATH):
            if not re.search(re.escape(section), _guide_text(path), re.IGNORECASE):
                missing_per_guide.append(f"{path} misses {section!r}")
    assert not missing_per_guide, (
        f"Store-relevant dossier sections missing from guides: {missing_per_guide}")

    # Genuinely shared sections: each must be referenced in at least one guide.
    combined_guides = (
        _guide_text(SUBMIT_CLAUDE_PATH) + "\n" + _guide_text(SUBMIT_OPENAI_PATH)
    )

    unreferenced = []
    for section in h2_sections:
        if not re.search(re.escape(section), combined_guides, re.IGNORECASE):
            unreferenced.append(section)

    assert not unreferenced, f"The following dossier sections are not referenced in any guide: {unreferenced}"


def test_guides_urls_liveness():
    combined_text = (
        _guide_text(SUBMIT_CLAUDE_PATH) + "\n" + _guide_text(SUBMIT_OPENAI_PATH)
    )

    # Match URLs stopping at space, closing paren/bracket, or trailing backtick
    raw_urls = set(re.findall(r"https?://[^\s\)>\]\",`]+", combined_text))
    # Strip any trailing punct/backtick just in case
    urls = {url.rstrip("`.,;") for url in raw_urls}
    assert urls, "No URLs found in submission guides"

    failed_urls = []
    pending_deploy = []
    for url in urls:
        host = urllib.parse.urlparse(url).netloc
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Python submission guide validator)"}
        )
        # Every push to main starts CI and the Coolify rebuild (Auto Deploy)
        # in parallel, and Traefik answers 503 while the container is being
        # swapped — a liveness check that calls that a broken link fails its
        # own push. Measured window: over 30 s, under 3 min. So for OUR host
        # a transient 5xx is a deploy window to wait out, bounded; every
        # other host is checked strictly, first answer counts.
        attempts = 9 if host in EXPECTED_PENDING_HOSTS else 1
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    code = resp.getcode()
                if code not in (200, 301, 302, 303, 307, 308):
                    failed_urls.append(f"{url} -> status {code}")
                break
            except urllib.error.HTTPError as e:
                # 403 / 401 login-gated URLs are accepted for portals (e.g., claude.ai / platform.openai.com)
                if e.code in (403, 401):
                    break
                # The OpenAI domain-challenge route 404s BY DESIGN until the
                # operator pastes the token OpenAI issues at submission time
                # (ODOO_REMOTE_OPENAI_CHALLENGE in the host's environment);
                # configured, the same URL answers 200 with that token.
                if (e.code == 404
                        and url.rstrip("/").endswith(
                            "/.well-known/openai-apps-challenge")):
                    pending_deploy.append(
                        f"{url} -> 404 — EXPECTED-UNTIL-CONFIGURED: "
                        f"ODOO_REMOTE_OPENAI_CHALLENGE is unset; it gets its"
                        f" value when OpenAI issues the challenge token")
                    break
                # The consent page answers 400 to a request carrying no
                # pending authorisation, which is the whole point of it: the
                # `req` id is 192 bits handed only to the browser that came
                # through /authorize, and a page that rendered a credential
                # form for anyone who typed the URL would be the bug. Cited
                # in a guide as the address the reviewer's client opens for
                # them, so the link is live and the refusal is correct.
                if e.code == 400 and url.rstrip("/").endswith("/consent"):
                    break
                if e.code in (502, 503, 504) and attempt + 1 < attempts:
                    time.sleep(15)
                    continue
                failed_urls.append(f"{url} -> HTTPError {e.code}")
            except Exception as e:
                # Every way a not-yet-deployed host can fail BEFORE answering
                # HTTP counts as pending, not as a broken link. Measured: from
                # a network whose resolver does not know the name it is
                # `socket.gaierror`, while from GitHub's runners the name DOES
                # resolve — to the Coolify host, whose Traefik answers
                # unrouted hostnames with its self-signed "TRAEFIK DEFAULT
                # CERT", i.e. an SSL verify failure. Same fact, two shapes;
                # pinning only the DNS one made this test pass locally and
                # fail in CI. A typo in one of these paths still fails
                # strictly once the host is live, because that arrives as an
                # HTTP status (404) and never reaches this branch.
                if host in EXPECTED_PENDING_HOSTS:
                    pending_deploy.append(
                        f"{url} -> {type(e).__name__} ({getattr(e, 'reason', e)}) "
                        f"— EXPECTED-PENDING-DEPLOY: the hosted server is not live yet")
                else:
                    failed_urls.append(f"{url} -> Exception {e}")
                break

    assert not failed_urls, (
        f"URL liveness check failed for: {failed_urls}"
        + (f" | EXPECTED-PENDING-DEPLOY (not failures): {pending_deploy}"
           if pending_deploy else ""))
    if pending_deploy:
        print("EXPECTED-PENDING-DEPLOY (not failures):", pending_deploy)


# ------------------------------------------------------------ the tool count
# Both guides tell the submitter how many tools the portal will find, and one
# of them lists every name. That number drifted from 19 to 22 unnoticed while
# the dossier stayed right, because the dossier is compared against the live
# server by test_listing_copy and these guides were compared against nothing.
def _live_tool_names() -> list[str]:
    import anyio

    from odoo_assistant import server

    server._register_all()
    return [tool.name for tool in anyio.run(server.mcp.list_tools)]


def test_every_tool_count_in_the_guides_matches_the_live_server():
    """A submitter reading "19 tools" and seeing 22 in the portal cannot tell
    which side is wrong. The count in the guides is the server's, or it is a
    lie that survives to the review."""
    expected = len(_live_tool_names())

    wrong = []
    for path in (SUBMIT_CLAUDE_PATH, SUBMIT_OPENAI_PATH):
        for count in re.findall(r"(\d+)\s+tools\b", _guide_text(path)):
            if int(count) != expected:
                wrong.append(f"{path}: says {count} tools, server exposes {expected}")
    assert not wrong, wrong


def test_the_openai_guide_lists_every_tool_by_name():
    """The MCP tab's snapshot is the reviewed contract, so the guide spells it
    out. A tool missing from that list is a tool nobody checked."""
    names = _live_tool_names()
    text = _guide_text(SUBMIT_OPENAI_PATH)

    missing = [name for name in names if f"`{name}`" not in text]
    assert not missing, f"{SUBMIT_OPENAI_PATH} never names: {missing}"
