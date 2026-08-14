"""Server skeleton: credential resolution, logging channel, SDK wiring."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from odoo_assistant import server
from odoo_client import MissingCredentials

CREDENTIAL_VARS = (
    "ODOO_BASE_URL",
    "ODOO_DB",
    "ODOO_USER",
    "ODOO_API_KEY",
)

# ODOO_USER is read but not demanded: the key identifies its own owner.
REQUIRED_VARS = (
    "ODOO_BASE_URL",
    "ODOO_DB",
    "ODOO_API_KEY",
)


@pytest.fixture
def clean_environment(monkeypatch):
    """Given: no Odoo credentials in the environment and no cached client."""
    for name in (*CREDENTIAL_VARS, "ODOO_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(server, "_odoo_instance", None)


def test_missing_credentials_are_named_one_by_one(clean_environment):
    """When nothing is set, the error names every variable the server needs."""
    with pytest.raises(MissingCredentials) as raised:
        server._get_odoo()

    message = str(raised.value)
    for name in REQUIRED_VARS:
        assert name in message
    assert "ODOO_USER" not in message


def test_api_key_is_required_and_blamed_alone(clean_environment, monkeypatch):
    """With only the API key missing, only the API key is reported missing."""
    monkeypatch.setenv("ODOO_BASE_URL", "http://odoo.invalid:8069")
    monkeypatch.setenv("ODOO_DB", "testdb")
    monkeypatch.setenv("ODOO_USER", "tester")

    with pytest.raises(MissingCredentials) as raised:
        server._get_odoo()

    listed = str(raised.value).split("Missing Odoo credentials: ", 1)[1]
    listed = listed.split(".", 1)[0]
    assert listed == "ODOO_API_KEY"


def test_a_password_is_ignored_and_cannot_stand_in_for_the_api_key(
    clean_environment, monkeypatch
):
    """A password authenticates in Odoo's key slot, so refusing it is on us."""
    monkeypatch.setenv("ODOO_BASE_URL", "http://odoo.invalid:8069")
    monkeypatch.setenv("ODOO_DB", "testdb")
    monkeypatch.setenv("ODOO_USER", "tester")
    monkeypatch.setenv("ODOO_PASSWORD", "account-password")

    with pytest.raises(MissingCredentials) as raised:
        server._get_odoo()

    message = str(raised.value)
    assert "ODOO_API_KEY" in message
    assert "account-password" not in message


def test_the_api_key_is_the_secret_that_reaches_the_client(
    clean_environment, monkeypatch
):
    """ODOO_API_KEY lands in the client's `key` slot, alongside a password."""
    monkeypatch.setenv("ODOO_BASE_URL", "http://odoo.invalid:8069")
    monkeypatch.setenv("ODOO_DB", "testdb")
    monkeypatch.setenv("ODOO_USER", "tester")
    monkeypatch.setenv("ODOO_PASSWORD", "account-password")
    monkeypatch.setenv("ODOO_API_KEY", "an-api-key")

    assert server._credentials().api_key == "an-api-key"


def test_the_login_is_optional_and_discovered_from_the_key(
    clean_environment, monkeypatch
):
    """Given only a URL, a database and a key, When credentials are read, Then
    the server accepts them: the client discovers the login from the key's
    owner (`odoo_client._discover_uid`), so requiring ODOO_USER here refuses a
    startup the transport supports."""
    monkeypatch.setenv("ODOO_BASE_URL", "http://odoo.invalid:8069")
    monkeypatch.setenv("ODOO_DB", "testdb")
    monkeypatch.setenv("ODOO_API_KEY", "an-api-key")

    creds = server._credentials()

    assert creds.user == ""
    assert creds.db == "testdb"


def test_logging_goes_to_stderr_and_stdout_stays_empty():
    """stdio transport: importing and logging must not touch stdout."""
    probe = "from odoo_assistant.server import logger; logger.warning('probe-marker')"
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert result.stdout == ""
    assert "probe-marker" in result.stderr


def test_server_instance_and_entry_point_are_wired():
    """The console script target and the SDK instance are importable."""
    from odoo_assistant.server import main, mcp

    assert type(mcp).__name__ == "MCPServer"
    assert callable(main)


def test_server_json_marks_the_login_optional():
    """Given the registry reads server.json to build a host's env prompt, When
    the login is optional to the server, Then the manifest must say so —
    otherwise a host refuses to launch without a value it does not need."""
    manifest = json.loads((Path(__file__).parents[1] / "server.json").read_text())
    variables = {
        v["name"]: v for v in manifest["packages"][0]["environmentVariables"]
    }
    assert variables["ODOO_USER"]["isRequired"] is False
    assert variables["ODOO_DB"]["isRequired"] is True
    assert variables["ODOO_API_KEY"]["isRequired"] is True

