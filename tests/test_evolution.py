"""Self-evolution tools: the REF_DIR redirect, generation, and what we know.

The redirect is the point of the module: `explore_module.py` writes next to its
own source, which resolves under `site-packages` once installed from a wheel —
read-only on most systems. Every test here runs with `HOME` pointed at a
`tmp_path`, so the suite can never write into the real
`~/.local/share/odoo-assistant/`, into `site-packages`, or into the repo's own
`references/`.
"""
from pathlib import Path

import anyio
import pytest
from mcp.server import MCPServer
from mcp.types import InputRequiredResult

from odoo_assistant import paths, tools_evolution
from odoo_assistant.odoo_scripts import explore_module as explorer
from odoo_assistant.server_errors import ToolExecutionError

NOTES_MARKER = "## NOTES — hand-written, preserved across regenerations"

SALE_FIELDS = {
    "name": {"string": "Order Reference", "type": "char"},
    "state": {"string": "Status", "type": "selection",
              "selection": [["draft", "Quotation"], ["sale", "Sales Order"]]},
    "amount_total": {"string": "Total", "type": "monetary"},
    "amount_total_signed": {"string": "Total Signed", "type": "monetary"},
    "partner_id": {"string": "Customer", "type": "many2one",
                   "relation": "res.partner"},
}


@pytest.fixture
def user_home(tmp_path, monkeypatch):
    """Given: HOME is a scratch directory, so the real one is never touched."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Records the real (import-time) value so teardown puts it back.
    monkeypatch.setattr(explorer, "REF_DIR", explorer.REF_DIR)
    # Same for the server a previous `register()` may have stashed: a test
    # must never publish onto the real `odoo_assistant.server` instance.
    monkeypatch.setattr(tools_evolution, "_mcp", None)
    tools_evolution._redirect_references()
    return home


@pytest.fixture
def instance(mock_odoo):
    """A MockOdoo programmed for every call `explore_module.generate` makes."""
    mock_odoo.db = "testdb"  # `generate` prints `odoo.db or odoo.base` in the header
    mock_odoo.base = "http://odoo.invalid:8069"
    mock_odoo.set_results("res.company", [{"id": 1}, {"id": 2}])
    mock_odoo.set_results("sale.order", 133, method="search_count")
    mock_odoo.set_results("sale.order", SALE_FIELDS, method="fields_get")
    mock_odoo.set_results("sale.order", [], method="read_group")
    mock_odoo.set_results("ir.actions.act_window", 4, method="search_count")
    mock_odoo.set_results("ir.ui.menu", [])       # menus_for
    mock_odoo.set_results("ir.model.fields", [])  # action_methods probe
    mock_odoo.set_results("ir.ui.view", [])       # action_methods form scan
    return mock_odoo


def _uris(mcp: MCPServer) -> set[str]:
    return {str(resource.uri) for resource in anyio.run(mcp.list_resources)}


def _read(mcp: MCPServer, uri: str) -> str:
    contents = anyio.run(mcp.read_resource, uri)
    assert not isinstance(contents, InputRequiredResult)
    return "".join(item.content for item in contents if isinstance(item.content, str))


def test_the_reference_dir_is_redirected_under_the_user_home():
    """Given the module is imported, When nothing else happens, Then the
    script writes under this platform's per-user data dir — never under
    site-packages, and never under a POSIX path guessed on Windows."""
    assert str(explorer.REF_DIR).startswith(str(paths.data_dir()))


def test_importing_the_module_creates_no_directory(user_home):
    """Given a fresh HOME, When the redirect is applied, Then no directory is
    created — importing the server must not touch the user's disk."""
    assert not Path(explorer.REF_DIR).exists()
    assert list(user_home.iterdir()) == []


def test_the_write_target_is_never_the_bundled_reference_set(user_home):
    """Given both directories resolve, When they are compared, Then the
    read-only bundle and the writable target are distinct places."""
    bundled = tools_evolution._bundled_references()
    target = Path(explorer.REF_DIR)
    assert bundled.is_dir()
    assert bundled != target
    assert not target.is_relative_to(Path(tools_evolution.__file__).parent)


def test_generate_writes_the_reference_under_the_redirected_dir(user_home, instance):
    """Given a live instance, When a module is generated, Then a `<module>.md`
    lands in the redirected directory with the auto half above the marker."""
    message = tools_evolution.explore_module("sales", "generate", models="sale.order")

    path = Path(explorer.REF_DIR) / "sales.md"
    assert path.is_file()
    assert str(path) in message
    text = path.read_text()
    head, _, notes = text.partition(NOTES_MARKER)
    assert "## Models and volumes" in head
    assert "| `sale.order` | 133 |" in head
    assert "`amount_total` → use **`amount_total_signed`**" in head
    assert notes  # the second half exists even on a first generation


@pytest.mark.parametrize(
    "hostile",
    ["/tmp/EVIL", "../../../tmp/EVIL2", "..", "a/b", "sales/../../escape", ".hidden", "sale\n"],
)
def test_a_name_that_is_not_a_module_slug_is_refused(user_home, instance, hostile):
    """Given a module name that is really a path, When generation is asked,
    Then it is refused and nothing is written where that name pointed — a
    tool caller must not be able to overwrite an arbitrary `.md` file."""
    would_be = Path(explorer.REF_DIR) / f"{hostile}.md"

    with pytest.raises(ToolExecutionError) as refusal:
        tools_evolution.explore_module(hostile, "generate", models="sale.order")

    assert "REFUSED" in str(refusal.value)
    assert not would_be.exists()
    assert not Path(explorer.REF_DIR).exists()


def test_a_real_odoo_module_slug_is_accepted(user_home, instance):
    """Given a name shaped like a real Odoo module, When it is generated,
    Then the guard lets it through — refusing must not cost the legitimate
    names it exists to protect."""
    tools_evolution.explore_module("l10n_cz", "generate", models="sale.order")

    assert (Path(explorer.REF_DIR) / "l10n_cz.md").is_file()


def test_the_default_action_generates(user_home, instance):
    """Given only a module name, When the tool runs, Then it generates the
    reference — PRD §5B and §19 both document `action="generate"` as the
    default, so a bare call must not silently return a ranking instead."""
    tools_evolution.explore_module("sales", models="sale.order")

    assert (Path(explorer.REF_DIR) / "sales.md").is_file()


def test_a_generated_reference_is_served_without_a_restart(user_home, instance):
    """Given a running server, When a module is generated, Then its
    `odoo://ref/<module>` resource answers immediately — PRD §19 promises the
    reference is available to all future queries, not after a restart."""
    mcp = MCPServer("test-evolution")
    tools_evolution.register(mcp)
    assert "odoo://ref/sales" not in _uris(mcp)

    tools_evolution.explore_module("sales", "generate", models="sale.order")

    assert "odoo://ref/sales" in _uris(mcp)
    assert "## Models and volumes" in _read(mcp, "odoo://ref/sales")


def test_direct_generation_reports_when_no_mcp_server_is_active(user_home, instance):
    """Given no registered MCP server, When generation runs directly, Then the
    result says the file was written but not registered as a resource."""
    message = tools_evolution.explore_module("sales", "generate", models="sale.order")

    assert "not registered as a resource because no MCP server is active" in message


def test_a_second_generate_preserves_hand_written_notes(user_home, instance):
    """Given a reference with a hand-added note, When it is regenerated, Then
    the note survives and everything above the marker is rebuilt."""
    tools_evolution.explore_module("sales", "generate", models="sale.order")
    path = Path(explorer.REF_DIR) / "sales.md"
    head, _, _ = path.read_text().partition(NOTES_MARKER)
    path.write_text(head + NOTES_MARKER + "\n\n- Pitfall: delivery blocks invoicing.\n")

    tools_evolution.explore_module("sales", "generate", models="sale.order")

    text = path.read_text()
    assert text.count(NOTES_MARKER) == 1
    assert "- Pitfall: delivery blocks invoicing." in text.split(NOTES_MARKER)[1]
    assert "## Models and volumes" in text.split(NOTES_MARKER)[0]


def test_generate_refuses_when_the_reference_blows_the_size_budget(user_home, mock_odoo):
    """Given a module whose reference would exceed 12 KB, When it is
    generated, Then the tool errors naming the offending section and writes
    nothing — an unloadable reference is worse than none."""
    bloated = {}
    for i in range(400):
        base = f"amount_untaxed_component_{i:03d}"
        bloated[base] = {"string": "Component", "type": "monetary"}
        bloated[f"{base}_signed"] = {"string": "Component signed", "type": "monetary"}
    mock_odoo.db = "testdb"
    mock_odoo.base = "http://odoo.invalid:8069"
    mock_odoo.set_results("res.company", [{"id": 1}])
    mock_odoo.set_results("sale.order", 133, method="search_count")
    mock_odoo.set_results("sale.order", bloated, method="fields_get")
    mock_odoo.set_results("ir.actions.act_window", 4, method="search_count")
    mock_odoo.set_results("ir.ui.menu", [])
    mock_odoo.set_results("ir.model.fields", [])
    mock_odoo.set_results("ir.ui.view", [])

    with pytest.raises(ToolExecutionError) as refusal:
        tools_evolution.explore_module("huge", "generate", models="sale.order")

    text = str(refusal.value)
    assert "REFUSED" in text
    assert "largest section:" in text
    assert "Sum the `_signed` twin" in text
    assert not (Path(explorer.REF_DIR) / "huge.md").exists()


def test_list_ranks_modules_without_printing_to_stdout(user_home, instance, capsys, monkeypatch):
    """Given the script prints its ranking, When the tool runs it, Then the
    table is returned and stdout stays clean — stdio carries only JSON-RPC."""
    monkeypatch.setattr(explorer, "KNOWN", {"sales": ["sale.order"]})

    result = tools_evolution.explore_module("sales", "list")

    assert "MODULE" in result and "SCORE" in result
    assert "sales" in result
    assert capsys.readouterr().out == ""


def test_an_unknown_module_names_the_alternatives(user_home, instance):
    """Given a module the script does not group, When generation is asked
    without models, Then the refusal lists what can be named instead."""
    with pytest.raises(ToolExecutionError) as refusal:
        tools_evolution.explore_module("superchat", "generate")

    text = str(refusal.value)
    assert "superchat" in text
    assert "helpdesk" in text
    assert 'models="a.b,c.d"' in text


def test_an_unknown_action_is_refused(user_home, instance):
    """Given a typo'd action, When the tool runs, Then it names the two valid
    ones instead of silently doing the default."""
    with pytest.raises(ToolExecutionError) as refusal:
        tools_evolution.explore_module("sales", "regenerate")

    assert "'list' or 'generate'" in str(refusal.value)


def test_list_known_modules_reports_bundled_and_generated_entries(user_home, instance):
    """Given one generated reference, When the knowledge base is listed, Then
    it carries both the bundled set and the generated one, with the date and
    the record count read from the file."""
    tools_evolution.explore_module("sales", "generate", models="sale.order")

    entries = tools_evolution.list_known_modules()

    assert '"source": "bundled"' in entries
    generated = [line for line in entries.split("},") if '"generated"' in line
                 and '"module": "sales"' in line]
    assert generated, entries
    assert '"records": 133' in generated[0]
    assert '"generated": null' not in generated[0]
