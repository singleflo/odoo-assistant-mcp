"""Where this server writes: one directory, resolved per platform.

MCP defines no location for a server's own files, and it deprecated `roots` in
2026-07-28 — "new implementations should pass directories or files via tool
parameters, resource URIs, or **server configuration** instead". So the
location is configuration with a sane default, never a path inferred from the
client or from the package's own install directory (a wheel under
`site-packages` is read-only on most systems).

The default follows each platform's own convention, because this package is
installed on Windows, macOS and Linux alike and `~/.local/share` is a Linux
answer that lands in a folder literally named `~` on Windows.

    Windows   %LOCALAPPDATA%\\odoo-assistant
    macOS     ~/Library/Application Support/odoo-assistant
    other     $XDG_DATA_HOME/odoo-assistant, else ~/.local/share/odoo-assistant

`ODOO_MCP_DATA_DIR` overrides all of it, which is what keeps everything this
server writes in one place the operator chose — and one place to back up or
wipe. Profiles could arguably live in a cache directory instead, since they
are rebuildable, but splitting them off would give the operator two locations
to reason about for no benefit they asked for.
"""
import os
import sys
from pathlib import Path

APP_NAME = "odoo-assistant"


def _from_env(name: str) -> Path | None:
    """An environment path, ignoring the empty string.

    XDG says an unset OR empty variable means "not configured"; treating "" as
    a path yields the process's own working directory, which is how a server
    ends up writing into whatever folder the host happened to launch it from.
    """
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


def data_dir() -> Path:
    """The directory this server may write into. Never created here: importing
    the server must not touch the user's disk."""
    override = _from_env("ODOO_MCP_DATA_DIR")
    if override:
        return override

    if sys.platform == "win32":
        base = _from_env("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return base / APP_NAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME

    base = _from_env("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return base / APP_NAME
