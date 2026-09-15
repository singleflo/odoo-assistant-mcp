"""Expiring download links for files produced on the hosted server.

The token IS the credential (custom routes are unauthenticated by SDK
design), so the tests here assert the things that make that safe, not just
a 200: the exact bytes, the no-store cache header, and — after expiry —
both the 404 AND the file's removal from disk.

`ttl_minutes=0` is the deterministic way to force expiry: the row's
expires_at is already in the past when the request arrives, no clock
monkeypatching needed.

The tool-level tests drive the REAL `Documents` over the `MockOdoo` double
from `tests/conftest.py`, exactly like `tests/test_tools_collab.py` does:
what changes is only the OUTPUT side (links instead of paths), so the
download chain below is the real one.
"""
import base64
import json
from typing import Literal

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from odoo_assistant import paths, tenant
from odoo_assistant.remote.files import publish, purge_expired_files, serve_file
from odoo_assistant.tools_collab import download_docs, generate_pdf
from tests.conftest import MockOdoo

PDF_BYTES = b"%PDF-1.4 pretend invoice"
PNG_BYTES = b"\x89PNG pretend image bytes"
PUBLIC_URL = "https://mcp.example.test"
SUBJECT = "t_test_subject"


@pytest.fixture(autouse=True)
def data_dir(monkeypatch, tmp_path):
    """Given: the server's data directory is this test's own tmp_path."""
    monkeypatch.setenv("ODOO_MCP_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def default_lists(monkeypatch):
    """Given: the lists a host did not configure — the documented defaults."""
    monkeypatch.delenv("ODOO_MCP_ALLOW", raising=False)
    monkeypatch.delenv("ODOO_MCP_DENY", raising=False)
    monkeypatch.delenv("ODOO_MCP_ALLOW_UNLINK", raising=False)


def client() -> TestClient:
    """A bare Starlette app with only the file route (todo 8 mounts it).

    The SDK's session-manager lifespan is not involved in a bare app, so a
    plain TestClient without entering it as a context manager works.
    """
    app = Starlette(routes=[
        Route("/files/{token}", serve_file, methods=["GET"])])
    return TestClient(app)


# ------------------------------------------------------------------- publish
def test_publish_answers_a_link_and_the_route_serves_the_exact_bytes(data_dir):
    """Given a produced file, When it is published and fetched,
    Then the bytes arrive with the attachment and no-store headers."""
    source = data_dir / "invoice.pdf"
    source.write_bytes(PDF_BYTES)

    link = publish(source, SUBJECT, public_url=PUBLIC_URL)

    assert set(link) == {"name", "size", "url", "expires_at"}
    assert link["name"] == "invoice.pdf"
    assert link["size"] == len(PDF_BYTES)
    assert link["url"].startswith(f"{PUBLIC_URL}/files/")
    token = link["url"].rsplit("/", 1)[1]

    response = client().get(f"/files/{token}")

    assert response.status_code == 200
    assert response.content == PDF_BYTES
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_an_expired_token_answers_404_and_the_file_is_gone(data_dir):
    """Given a link past its TTL, When it is fetched,
    Then the answer is 404 AND the file no longer exists on disk.

    Both halves matter: a 404 that leaves the bytes in place keeps a
    tenant's documents sitting in the data directory forever.
    """
    source = data_dir / "secret.pdf"
    source.write_bytes(PDF_BYTES)
    token = publish(
        source, SUBJECT, public_url=PUBLIC_URL, ttl_minutes=0
    )["url"].rsplit("/", 1)[1]

    response = client().get(f"/files/{token}")

    assert response.status_code == 404
    assert response.json() == {"error": "not found or expired"}
    assert not any((data_dir / "files" / SUBJECT).iterdir())


def test_an_unknown_token_answers_404(data_dir):
    """Given a token nothing published, When it is fetched, Then 404 JSON."""
    response = client().get("/files/not-a-real-token")

    assert response.status_code == 404
    assert response.json() == {"error": "not found or expired"}


def test_a_path_traversal_url_never_reaches_the_handler(data_dir):
    """Given /files/../etc/passwd, When it is fetched, Then 404.

    Starlette's {token} path parameter cannot contain a slash, so dot
    segments are normalised away before routing and there is no handler to
    attack; the handler's own defence is the exact-match token lookup.
    """
    assert client().get("/files/../etc/passwd").status_code == 404


def test_publish_refuses_a_subject_that_could_escape_the_files_root(data_dir):
    """Given a subject carrying a separator or dot segments, When published,
    Then ValueError — the seam is public to future callers, and the subject
    becomes a directory name under data_dir()/files/."""
    source = data_dir / "x.pdf"
    source.write_bytes(PDF_BYTES)
    for bad in ("../escape", "a/b", "..", ""):
        with pytest.raises(ValueError):
            publish(source, bad, public_url=PUBLIC_URL)


def test_purge_removes_only_the_expired_rows_and_directories(data_dir):
    """Given one expired and one live file, When purging,
    Then the expired row and its directory go and the live one survives."""
    stale = data_dir / "stale.pdf"
    stale.write_bytes(PDF_BYTES)
    live = data_dir / "live.pdf"
    live.write_bytes(PNG_BYTES)
    stale_token = publish(
        stale, SUBJECT, public_url=PUBLIC_URL, ttl_minutes=0
    )["url"].rsplit("/", 1)[1]
    live_token = publish(live, SUBJECT, public_url=PUBLIC_URL)[
        "url"].rsplit("/", 1)[1]

    assert purge_expired_files() == 1

    assert not (data_dir / "files" / SUBJECT / stale_token).exists()
    assert (data_dir / "files" / SUBJECT / live_token / "live.pdf").exists()
    assert client().get(f"/files/{live_token}").status_code == 200


# ------------------------------------------------------- download_docs (link)
def program_two_documents(odoo: MockOdoo) -> None:
    """One chatter attachment plus one binary field — two files on disk."""
    odoo.set_results("ir.attachment", [
        {"id": 1, "name": "quote.pdf", "mimetype": "application/pdf",
         "file_size": 24, "create_date": "2026-08-01", "type": "binary"}])
    odoo.set_results("ir.attachment", [
        {"name": "quote.pdf", "datas": base64.b64encode(PDF_BYTES).decode(),
         "mimetype": "application/pdf", "store_fname": "a/b", "file_size": 24}],
        method="read")
    odoo.set_results("sale.order", {"image": {"type": "binary"}},
                     method="fields_get")
    odoo.set_results("sale.order", [
        {"image": base64.b64encode(PNG_BYTES).decode()}], method="read")


def bound_tenant(
        monkeypatch, odoo: MockOdoo,
        policy: Literal["read", "standard"] = "read"):
    """Wire the double client and the public URL; hand back the bind token."""
    from odoo_assistant import server

    monkeypatch.setattr(server, "_odoo_instance", None)
    monkeypatch.setattr(server, "_get_odoo", lambda: odoo)
    monkeypatch.setenv("ODOO_REMOTE_PUBLIC_URL", PUBLIC_URL)
    return tenant.bind(tenant.Tenant(
        SUBJECT, "http://odoo.invalid", "key", "", policy))


def test_download_docs_under_a_tenant_returns_links_and_no_path(
        monkeypatch, data_dir, tmp_path):
    """Given a bound tenant, When documents are downloaded,
    Then every result is a URL — no filesystem path leaves the server.

    claude.ai caps tool results around 150k characters; a saved file's
    absolute path is also useless to a remote user who cannot read the
    server's disk.
    """
    odoo = MockOdoo()
    program_two_documents(odoo)
    token = bound_tenant(monkeypatch, odoo)
    try:
        payload = json.loads(download_docs("sale.order", 42, "/ignored"))
    finally:
        tenant.reset(token)

    assert len(payload["files"]) == 2
    for link in payload["files"]:
        assert link["url"].startswith(f"{PUBLIC_URL}/files/")
        assert "http" in link["url"]
        assert "/" not in link["name"]
    assert payload["skipped"] == []
    assert str(tmp_path) not in json.dumps(payload)
    assert "saved" not in payload


def test_generate_pdf_under_a_tenant_returns_a_link(
        monkeypatch, data_dir, tmp_path):
    """Given a bound tenant, When a PDF is generated,
    Then the answer is its download link, not the server-side path."""
    odoo = MockOdoo()
    odoo.set_results("ir.attachment", [
        {"id": 1, "name": "FT_2026_0062.pdf", "mimetype": "application/pdf",
         "file_size": 24, "create_date": "2026-08-01", "type": "binary"}])
    odoo.set_results("ir.attachment", [
        {"name": "FT_2026_0062.pdf",
         "datas": base64.b64encode(PDF_BYTES).decode(),
         "mimetype": "application/pdf", "store_fname": "a/b",
         "file_size": 24}], method="read")
    # "standard": generating a PDF runs the send/print wizard, which the
    # read-only policy refuses — correctly, and the refusal is tested in
    # test_tools_collab.py. Here the tenant allows it.
    token = bound_tenant(monkeypatch, odoo, policy="standard")
    try:
        payload = json.loads(generate_pdf("account.move", 5775, "/ignored"))
    finally:
        tenant.reset(token)

    assert payload["url"].startswith(f"{PUBLIC_URL}/files/")
    assert "path" not in payload
    assert str(tmp_path) not in json.dumps(payload)


def test_download_docs_without_a_tenant_keeps_the_local_contract(
        monkeypatch, data_dir, tmp_path):
    """Given no bound tenant (plain stdio), When documents are downloaded,
    Then dest_dir is honoured and the payload is the plain {"saved": ...}."""
    odoo = MockOdoo()
    program_two_documents(odoo)
    monkeypatch.setattr("odoo_assistant.server._odoo_instance", None)
    monkeypatch.setattr(
        "odoo_assistant.server._get_odoo", lambda: odoo)

    payload = json.loads(
        download_docs("sale.order", 42, str(tmp_path / "docs")))

    assert sorted(p.name for p in (tmp_path / "docs").iterdir()) == [
        "quote.pdf", "sale.order_42_image.bin"]
    assert payload["saved"] == [
        str(tmp_path / "docs" / "quote.pdf"),
        str(tmp_path / "docs" / "sale.order_42_image.bin")]
    assert "files" not in payload
    assert paths.data_dir() == data_dir      # nothing was written there
