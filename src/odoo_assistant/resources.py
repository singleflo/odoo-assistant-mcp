"""MCP resources: the Odoo methodology docs served as `odoo://*` (PRD §5).

`odoo://skill` is the entry point (the bundled `SKILL.md`); `odoo://ref/<stem>`
serves every file of the public reference bundle plus whatever this instance
has generated locally.

**Where the bundle lives depends on how the package was installed.** Hatchling
force-includes `references_public/` as `odoo_assistant/references/`
(pyproject.toml), but a force-include is a *build-time* copy into the wheel —
nothing puts those files under `src/odoo_assistant/` in a dev checkout. So:

* installed from a wheel → `importlib.resources.files("odoo_assistant")` has a
  `references` child, and that is what we serve;
* dev checkout / editable install → it does not, and we fall back to the
  repository's own `references_public/` directory, two levels above this file.

`importlib.resources` is asked first, so an installed server never depends on a
source layout that is not shipped.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_assistant import paths

if TYPE_CHECKING:
    from mcp.server import MCPServer

SKILL_URI = "odoo://skill"
REF_URI_PREFIX = "odoo://ref/"
MARKDOWN = "text/markdown"
SKILL_FILE = "SKILL.md"

# Where `explore_module` writes the references it generates for THIS instance.
# Absent until something has been generated — that is a normal state, not an
# error, so registration simply finds nothing there.
USER_REFERENCES_DIR = paths.data_dir() / "references"


def bundled_references() -> Traversable:
    """The public reference bundle: the wheel's copy, else the checkout's."""
    packaged = files("odoo_assistant").joinpath("references")
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parent.parent.parent / "references_public"


def _markdown_files(directory: Traversable) -> Iterator[Traversable]:
    """Every `.md` file in `directory`, by name — nothing if it does not exist."""
    if not directory.is_dir():
        return
    markdown = [entry for entry in directory.iterdir() if entry.name.endswith(".md")]
    yield from sorted(markdown, key=lambda entry: entry.name)


def _reader(source: Traversable) -> Callable[[], str]:
    """Read `source` when the resource is *read*, not when it is registered.

    The generated references are rewritten in place by `explore_module`, so the
    lazy read is what keeps a long-running server serving current content.
    """

    def read() -> str:
        return source.read_text(encoding="utf-8")

    return read


def register_reference(mcp: MCPServer, source: Traversable) -> None:
    """Serve one reference file as `odoo://ref/<stem>`.

    Called for every file found at startup, and again by `explore_module` the
    moment it generates a new one: the SDK asks its resource manager on every
    `resources/list` and `resources/read`, so a file registered mid-session is
    served without a restart (PRD §19 step 4). Registering a URI twice keeps
    the first registration, so a regeneration is a no-op here.
    """
    stem = source.name.removesuffix(".md")
    mcp.resource(
        REF_URI_PREFIX + stem,
        name=f"odoo-ref-{stem}",
        title=f"Odoo reference: {stem}",
        description=f"Odoo methodology reference: {stem}.",
        mime_type=MARKDOWN,
    )(_reader(source))


def register(mcp: MCPServer) -> None:
    """Register `odoo://skill` and one `odoo://ref/<stem>` per reference file.

    The bundled set is registered first: on a duplicate URI the SDK keeps the
    first registration, so a generated file can never shadow the audited public
    bundle. A URI nobody registered stays unknown, which is what makes the SDK
    answer `resources/read` with `-32602` as the spec requires — this module
    deliberately registers no catch-all template.
    """
    bundled = bundled_references()

    mcp.resource(
        SKILL_URI,
        name="odoo-skill",
        title="Odoo assistant skill",
        description=(
            "How to operate an Odoo instance: the safety rules, the query and "
            "write patterns, and the verification methodology."
        ),
        mime_type=MARKDOWN,
    )(_reader(bundled.joinpath(SKILL_FILE)))

    for source in (*_markdown_files(bundled), *_markdown_files(USER_REFERENCES_DIR)):
        register_reference(mcp, source)
