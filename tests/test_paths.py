"""Where this server is allowed to write, on every platform it installs on.

MCP deprecated `roots` in 2026-07-28 — "new implementations should pass
directories or files via tool parameters, resource URIs, or server
configuration instead" — so the location is configuration with a per-OS
default, never a path inferred from the client.
"""
import pytest

from odoo_assistant import paths


@pytest.fixture(autouse=True)
def no_inherited_override(monkeypatch):
    """Given: none of the location variables set by the developer's shell."""
    for name in ("ODOO_MCP_DATA_DIR", "XDG_DATA_HOME", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.delenv(name, raising=False)


def test_windows_writes_under_localappdata(monkeypatch):
    """Given Windows, When the data directory is resolved, Then it is the
    per-user application data folder, not a POSIX path that lands in a folder
    literally named `~` beside the process."""
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\ana\AppData\Local")

    resolved = paths.data_dir()

    assert "AppData" in str(resolved)
    assert resolved.name == "odoo-assistant"


def test_macos_writes_under_application_support(monkeypatch):
    """Given macOS, When resolved, Then Application Support — the location the
    MCP documentation itself uses for per-user files on this platform."""
    monkeypatch.setattr(paths.sys, "platform", "darwin")

    resolved = paths.data_dir()

    assert resolved.parent.as_posix().endswith("Library/Application Support")
    assert resolved.name == "odoo-assistant"


def test_linux_honours_xdg_data_home(monkeypatch):
    """Given Linux with XDG configured, When resolved, Then the user's choice
    wins over the ~/.local/share fallback."""
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/srv/data")

    assert paths.data_dir().as_posix() == "/srv/data/odoo-assistant"


def test_an_explicit_setting_wins_on_every_platform(monkeypatch):
    """Given the operator named a directory, When resolved on any platform,
    Then that directory is used — the configuration the spec points to."""
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    monkeypatch.setenv("ODOO_MCP_DATA_DIR", "/opt/odoo-mcp")

    assert paths.data_dir().as_posix() == "/opt/odoo-mcp"


def test_profiles_and_references_live_under_one_root(monkeypatch, tmp_path):
    """Given a configured data directory, When both writing features resolve
    their paths, Then both sit under it — one place to back up, one to wipe,
    and nothing left in a directory the user never chose."""
    monkeypatch.setenv("ODOO_MCP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ODOO_PROFILE_DIR", raising=False)

    from odoo_assistant import tools_evolution, tools_read

    assert tools_read._redirect_profiles().startswith(str(tmp_path))
    assert tools_evolution._redirect_references().startswith(str(tmp_path))
