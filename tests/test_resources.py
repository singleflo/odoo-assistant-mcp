"""Resources: `odoo://skill` + `odoo://ref/*` (plan todo 14, PRD §5).

Each test builds its own throwaway `MCPServer` — the module under test only
ever registers onto the server it is handed, so nothing here touches the real
`odoo_assistant.server` instance that the other waves are wiring in parallel.
"""

from pathlib import Path

import anyio
import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError
from mcp.types import INVALID_PARAMS, InputRequiredResult

from odoo_assistant import resources
from odoo_assistant.resources import bundled_references, register

BUNDLE = Path(__file__).resolve().parent.parent / "references_public"


@pytest.fixture(autouse=True)
def no_generated_references(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the per-instance reference directory at a path that does not exist.

    Without this the developer's own `~/.local/share/odoo-assistant/references/`
    would leak into the exact-URI-set assertions on whichever machine has one.
    """
    monkeypatch.setattr(resources, "USER_REFERENCES_DIR", tmp_path / "absent")


def _server() -> MCPServer:
    mcp = MCPServer("test-resources")
    register(mcp)
    return mcp


def _uris(mcp: MCPServer) -> set[str]:
    return {str(resource.uri) for resource in anyio.run(mcp.list_resources)}


def _read(mcp: MCPServer, uri: str) -> str:
    contents = anyio.run(mcp.read_resource, uri)
    # Static files never ask the client for input, so the multi-round-trip
    # branch of the return union cannot happen here.
    assert not isinstance(contents, InputRequiredResult)
    return "".join(item.content for item in contents if isinstance(item.content, str))


def test_skill_resource_serves_the_bundled_skill_file() -> None:
    # Given a server with the resources registered
    mcp = _server()
    # When odoo://skill is read
    text = _read(mcp, "odoo://skill")
    # Then it is the bundled SKILL.md, frontmatter first
    assert "odoo://skill" in _uris(mcp)
    assert text.startswith("---\nname: odoo\n")
    assert text == (BUNDLE / "SKILL.md").read_text(encoding="utf-8")


def test_every_bundled_reference_file_becomes_a_ref_resource() -> None:
    # Given the public bundle on disk
    expected = {f"odoo://ref/{path.stem}" for path in BUNDLE.glob("*.md")}
    assert len(expected) > 1, "the bundle is empty — the assertion below is vacuous"
    # When the resources are registered
    mcp = _server()
    # Then every file has its own odoo://ref/<stem>, and nothing else does
    assert {uri for uri in _uris(mcp) if uri.startswith("odoo://ref/")} == expected


def test_reading_a_reference_returns_the_file_verbatim() -> None:
    # Given a server with the resources registered
    mcp = _server()
    # When odoo://ref/writing is read
    text = _read(mcp, "odoo://ref/writing")
    # Then it is exactly writing.md
    assert text == (BUNDLE / "writing.md").read_text(encoding="utf-8")


def test_unknown_reference_raises_the_spec_not_found_signal() -> None:
    """The spec (server/resources.md §Error Handling) mandates JSON-RPC -32602
    for a resource that does not exist, and forbids an empty `contents` array.

    Registering only concrete resources — no catch-all template — is what earns
    that: the miss reaches the SDK's `ResourceNotFoundError`, which its
    `_handle_read_resource` turns into `MCPError(INVALID_PARAMS)` on the wire
    (verified end to end in the task evidence file).
    """
    # Given a server with the resources registered
    mcp = _server()
    # When a URI nobody registered is read
    with pytest.raises(ResourceNotFoundError):
        anyio.run(mcp.read_resource, "odoo://ref/doesnotexist")
    # Then the signal the SDK maps carries the code the spec mandates
    assert INVALID_PARAMS == -32602


def test_missing_user_directory_does_not_break_registration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given a per-instance reference directory that was never created
    absent = tmp_path / "never-generated"
    monkeypatch.setattr(resources, "USER_REFERENCES_DIR", absent)
    assert not absent.exists()
    # When the resources are registered
    mcp = _server()
    # Then the bundled set is served as usual
    assert "odoo://skill" in _uris(mcp)
    assert "odoo://ref/writing" in _uris(mcp)


def test_generated_references_are_registered_alongside_the_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given this instance has generated a module reference
    generated = tmp_path / "references"
    generated.mkdir()
    (generated / "sale_order.md").write_text("# sale.order\n", encoding="utf-8")
    (generated / "notes.txt").write_text("not markdown\n", encoding="utf-8")
    monkeypatch.setattr(resources, "USER_REFERENCES_DIR", generated)
    # When the resources are registered
    mcp = _server()
    # Then the generated markdown joins the same URI scheme, and only markdown does
    assert _read(mcp, "odoo://ref/sale_order") == "# sale.order\n"
    assert "odoo://ref/notes" not in _uris(mcp)


def test_a_generated_file_cannot_shadow_the_bundled_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given a generated file whose stem collides with a bundled reference
    generated = tmp_path / "references"
    generated.mkdir()
    (generated / "writing.md").write_text("# impostor\n", encoding="utf-8")
    monkeypatch.setattr(resources, "USER_REFERENCES_DIR", generated)
    # When the resources are registered
    mcp = _server()
    # Then the audited bundle still wins — the bundle is registered first
    assert _read(mcp, "odoo://ref/writing") == (BUNDLE / "writing.md").read_text(encoding="utf-8")


def test_bundle_resolves_from_the_checkout_when_the_wheel_copy_is_absent() -> None:
    # Given a dev checkout, where hatchling's build-time force-include has not run
    packaged = resources.files("odoo_assistant").joinpath("references")
    assert not packaged.is_dir(), "unexpected: the wheel layout exists in the checkout"
    # When the bundle is located
    located = bundled_references()
    # Then it is the repository's own references_public/
    assert Path(str(located)) == BUNDLE
    assert located.joinpath("SKILL.md").is_file()


def test_bundle_prefers_the_packaged_copy_when_one_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The wheel branch, exercised without building a wheel: stand in a package
    root that *does* carry a `references` child and prove it is chosen.
    """
    # Given importlib.resources can find odoo_assistant/references
    package_root = tmp_path / "odoo_assistant"
    (package_root / "references").mkdir(parents=True)
    (package_root / "references" / "SKILL.md").write_text("---\nname: odoo\n", encoding="utf-8")
    monkeypatch.setattr(resources, "files", lambda _package: package_root)
    # When the bundle is located
    located = bundled_references()
    # Then the packaged copy wins over the checkout
    assert Path(str(located)) == package_root / "references"
    assert _read(_server(), "odoo://skill") == "---\nname: odoo\n"


def test_content_is_read_when_the_resource_is_read_not_when_registered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given a registered generated reference
    generated = tmp_path / "references"
    generated.mkdir()
    target = generated / "explored.md"
    target.write_text("# first pass\n", encoding="utf-8")
    monkeypatch.setattr(resources, "USER_REFERENCES_DIR", generated)
    mcp = _server()
    # When explore_module rewrites the file under a running server
    target.write_text("# second pass\n", encoding="utf-8")
    # Then the next read serves the new content
    assert _read(mcp, "odoo://ref/explored") == "# second pass\n"


def test_every_resource_is_announced_as_markdown() -> None:
    # Given a server with the resources registered
    mcp = _server()
    # When the resource list is inspected
    listed = anyio.run(mcp.list_resources)
    # Then every entry carries a name and the markdown media type
    assert listed
    assert {resource.mime_type for resource in listed} == {"text/markdown"}
    assert all(resource.name for resource in listed)
