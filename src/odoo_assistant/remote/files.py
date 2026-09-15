#!/usr/bin/env python3
"""Expiring download links for files produced on the hosted server.

Claude caps tool results around 150k characters
(claude.com/docs/connectors/building#technical-specifications); a 4 MiB PDF
as base64 is ~5.3M chars, so blobs are out. A file a tool produces on the
hosted server is instead MOVED under `data_dir()/files/<subject>/<token>/`
and answered with a URL that serves it for `TTL_MINUTES`.

The token IS the credential: custom routes are unauthenticated by SDK design
(`mcpserver/server.py:1030-1032`), so the response carries
`Cache-Control: private, no-store` — no shared cache may keep a tenant's
invoice — and tokens are `secrets.token_urlsafe(24)` (128 bits of entropy,
unguessable, short-lived). Expired access answers 404 AND deletes the file;
`purge_expired_files()` does the same sweep for tokens nobody ever came back
for (called at startup and every 50th request).

This module owns its own table `files` in the SAME database file the consent
store uses (`data_dir()/remote.db`), opened per call, WAL, CREATE IF NOT
EXISTS — safe to coexist with `remote/store.py`'s tables, which this module
never touches.
"""
import itertools
import mimetypes
import secrets
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from odoo_assistant.paths import data_dir

TTL_MINUTES = 15
_NOT_FOUND = {"error": "not found or expired"}
_hits = itertools.count(1)
_base_dir = data_dir()


def configure_data_dir(path: Path) -> None:
    """Set the hosted process' file and metadata directory."""
    global _base_dir
    _base_dir = path


def _db() -> sqlite3.Connection:
    """One fresh connection per call, table ensured; close the returned one."""
    _base_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_base_dir / "remote.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS files ("
        "token TEXT PRIMARY KEY, subject TEXT NOT NULL, path TEXT NOT NULL, "
        "name TEXT NOT NULL, size INTEGER NOT NULL, expires_at TEXT NOT NULL)")
    conn.commit()
    return conn


def _remove(token: str, stored_path: str) -> None:
    """Delete one published file: first its directory, then its row."""
    shutil.rmtree(Path(stored_path).parent, ignore_errors=True)
    with closing(_db()) as conn:
        conn.execute("DELETE FROM files WHERE token = ?", (token,))
        conn.commit()


def publish(path: Path, subject: str, *, public_url: str,
            ttl_minutes: int = TTL_MINUTES) -> dict:
    """Move a produced file into the tenant's files area, answer its link.

    The file is MOVED, not copied: the tool's work directory holds nothing
    worth keeping once the link exists. `subject` becomes a directory name,
    so a value carrying "/" or ".." is refused before it can escape
    `data_dir()/files/` — the seam is public to future callers, and the
    server-generated subjects ("t_"+token_urlsafe(16)) are fine but are not
    trusted for that.
    """
    if not subject or "/" in subject or ".." in subject:
        raise ValueError(
            f"subject must be a plain directory name, got {subject!r}")
    token = secrets.token_urlsafe(24)
    dest_dir = _base_dir / "files" / subject / token
    dest_dir.mkdir(parents=True)
    dest = dest_dir / path.name
    shutil.move(str(path), dest)
    expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    with closing(_db()) as conn:
        conn.execute(
            "INSERT INTO files (token, subject, path, name, size, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (token, subject, str(dest), path.name, dest.stat().st_size,
             expires.isoformat()))
        conn.commit()
    return {
        "name": path.name,
        "size": dest.stat().st_size,
        "url": f"{public_url}/files/{token}",
        "expires_at": expires.isoformat(),
    }


async def serve_file(request: Request) -> Response:
    """GET /files/{token} — todo 8 mounts this as a custom route.

    Unknown token, expired token and a row whose bytes are gone all answer
    the same 404: an absent file must be indistinguishable from an expired
    link. An expired hit also removes the file right away, so the bytes do
    not wait for the next purge to disappear.
    """
    if next(_hits) % 50 == 0:
        purge_expired_files()
    token = request.path_params["token"]
    with closing(_db()) as conn:
        row = conn.execute(
            "SELECT path, name, expires_at FROM files WHERE token = ?",
            (token,)).fetchone()
    if row is None:
        return JSONResponse(_NOT_FOUND, status_code=404)
    stored, name, expires_at = row
    if datetime.fromisoformat(expires_at) <= datetime.now(timezone.utc):
        _remove(token, stored)
        return JSONResponse(_NOT_FOUND, status_code=404)
    file = Path(stored)
    if not file.is_file():                     # crash between rmtree and DELETE
        return JSONResponse(_NOT_FOUND, status_code=404)
    return FileResponse(
        file,
        media_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=utf-8''{quote(name)}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def purge_expired_files() -> int:
    """Delete expired rows and their token directories; returns the count."""
    now = datetime.now(timezone.utc)
    expired: list[tuple[str, str]] = []
    with closing(_db()) as conn:
        for token, stored, expires_at in conn.execute(
                "SELECT token, path, expires_at FROM files").fetchall():
            if datetime.fromisoformat(expires_at) <= now:
                expired.append((token, stored))
    for token, stored in expired:
        _remove(token, stored)
    return len(expired)
