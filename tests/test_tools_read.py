"""The four read tools: what reaches Odoo, and what comes back.

Every test asserts on the call that was actually recorded, not on the fact that
something was returned — a read tool that quietly drops the company context or
the limit still returns plausible rows.
"""
import asyncio

import pytest
from mcp.server import MCPServer

from odoo_assistant import tools_read
from odoo_assistant.server_errors import MAX_RESULT_CHARS, TRUNCATION_NOTICE, ToolExecutionError
from odoo_client import OdooError

PROFILE = {
    "fingerprint": {
        "odoo_version": "18.0",
        "edition": "enterprise",
        "module_count": 285,
        "transport": "xmlrpc",
        "taken_at": "2026-08-13T10:00:00",
    },
    "companies": [{"id": 2, "name": "Alpha S.L.", "country_id": [68, "Spain"]}],
    "areas": {"accounting": {"invoices_OUT": 377, "journal_entries": 3613}},
    "anomalies": {"overdue_invoices": 4},
}


def test_search_read_passes_model_domain_and_fields_through(mock_odoo):
    """Given a programmed model, When searched, Then Odoo sees that exact query."""
    mock_odoo.set_results("sale.order", [{"id": 1, "name": "SO001"}])

    out = tools_read.search_read("sale.order", [["state", "=", "sale"]], ["name"])

    assert mock_odoo.last_call["method"] == "search_read"
    assert mock_odoo.last_call["domain"] == [["state", "=", "sale"]]
    assert mock_odoo.last_call["kwargs"]["fields"] == ["name"]
    assert out == '[{"id": 1, "name": "SO001"}]'


def test_search_read_caps_a_runaway_limit_at_200(mock_odoo):
    """Given limit=999, When searched, Then Odoo is asked for 200."""
    mock_odoo.set_results("sale.order", [])

    tools_read.search_read("sale.order", [], limit=999)

    assert mock_odoo.last_call["kwargs"]["limit"] == tools_read.MAX_LIMIT


def test_search_read_floors_a_zero_limit_so_the_cap_cannot_be_bypassed(mock_odoo):
    """Given limit=0 — which Odoo reads as 'no limit' — Then it is not honoured."""
    mock_odoo.set_results("sale.order", [])

    tools_read.search_read("sale.order", [], limit=0)

    assert mock_odoo.last_call["kwargs"]["limit"] == 1


def test_search_read_maps_company_ids_onto_the_allowed_company_context(mock_odoo):
    """Given two companies, When searched, Then both reach Odoo as context."""
    mock_odoo.set_results("project.project", [])

    tools_read.search_read("project.project", [], company_ids=[1, 2])

    assert mock_odoo.last_call["kwargs"]["context"] == {"allowed_company_ids": [1, 2]}


def test_search_read_pages_with_offset(mock_odoo):
    """Given offset=80, When searched, Then the second page is requested."""
    mock_odoo.set_results("sale.order", [])

    tools_read.search_read("sale.order", [], offset=80)

    assert mock_odoo.last_call["kwargs"]["offset"] == 80


def test_search_read_omits_offset_on_the_first_page(mock_odoo):
    """Given no offset, When searched, Then no offset is sent at all."""
    mock_odoo.set_results("sale.order", [])

    tools_read.search_read("sale.order", [])

    assert "offset" not in mock_odoo.last_call["kwargs"]


def test_a_huge_result_is_truncated_with_the_paging_advice(mock_odoo):
    """Given a result far past the cap, When searched, Then it is cut with advice."""
    mock_odoo.set_results("sale.order", [{"id": n, "name": "x" * 60} for n in range(400)])

    out = tools_read.search_read("sale.order", [])

    assert len(out) == MAX_RESULT_CHARS + len(TRUNCATION_NOTICE)
    assert out.endswith(TRUNCATION_NOTICE)


def test_read_record_asks_only_for_fields_the_model_has(mock_odoo):
    """Given a model without payment_state, When read, Then it is not requested.

    Asking for a field a model does not define fails the whole read — which is
    how a run once lost an invoice's real state and reported a guess.
    """
    mock_odoo.set_results("sale.order", {"display_name": {}, "name": {}, "state": {}},
                          method="fields_get")
    mock_odoo.set_results("sale.order", [{"id": 7, "state": "sale"}], method="read")

    out = tools_read.read_record("sale.order", 7)

    assert mock_odoo.last_call["args"] == [[7], ["display_name", "name", "state"]]
    assert out == '[{"id": 7, "state": "sale"}]'


def test_read_record_uses_the_named_fields_when_given(mock_odoo):
    """Given explicit fields, When read, Then no introspection call is made."""
    mock_odoo.set_results("res.partner", [{"id": 3, "email": "a@b.c"}], method="read")

    tools_read.read_record("res.partner", 3, ["email"])

    assert [call["method"] for call in mock_odoo.calls] == ["read"]
    assert mock_odoo.last_call["args"] == [[3], ["email"]]


def test_count_records_returns_the_number_in_the_named_companies(mock_odoo):
    """Given company_ids, When counted, Then the context carries them."""
    mock_odoo.set_results("project.project", 87, method="search_count")

    out = tools_read.count_records("project.project", company_ids=[1, 2])

    assert out == "87"
    assert mock_odoo.last_call["kwargs"]["context"] == {"allowed_company_ids": [1, 2]}


def test_count_records_defaults_to_an_empty_domain(mock_odoo):
    """Given no domain, When counted, Then Odoo receives [] and no context."""
    mock_odoo.set_results("crm.lead", 84, method="search_count")

    tools_read.count_records("crm.lead")

    assert mock_odoo.last_call["domain"] == []
    assert mock_odoo.last_call["kwargs"] == {}


def test_an_odoo_refusal_becomes_an_error_result(mock_odoo):
    """Given Odoo rejects the call, When searched, Then the tool raises (isError)."""
    mock_odoo.set_results("sale.order", OdooError("sale.order.search_read: Invalid field"))

    with pytest.raises(ToolExecutionError) as raised:
        tools_read.search_read("sale.order", [["nope", "=", 1]])

    assert "Invalid field" in str(raised.value)
    assert "repeating the same call cannot succeed" in str(raised.value)


@pytest.mark.parametrize("read_it", [
    pytest.param(lambda: tools_read.search_read("account.move", [["state", "=", "posted"]]),
                 id="search_read"),
    pytest.param(lambda: tools_read.count_records("account.move"), id="count_records"),
    pytest.param(lambda: tools_read.read_record("account.move", 42), id="read_record"),
])
def test_an_unfiltered_account_move_query_is_refused_before_odoo_is_touched(mock_odoo, read_it):
    """Given no move_type filter, When any read runs, Then it is refused, unsent.

    That table mixes invoices, bills, credit notes and journal entries; 3.613
    records where the user sees 373.
    """
    with pytest.raises(ToolExecutionError) as raised:
        read_it()

    assert "move_type" in str(raised.value)
    assert mock_odoo.calls == []


def test_instance_overview_reports_the_profile_without_printing(monkeypatch, capsys):
    """Given a built profile, When asked, Then the report is returned, not printed."""
    monkeypatch.setattr(tools_read.query, "load", lambda: (PROFILE, "/tmp/testdb.json"))

    out = tools_read.instance_overview()

    assert "Odoo 18.0 enterprise" in out
    assert "[2] Alpha S.L. (Spain)" in out
    assert "invoices_OUT=377" in out
    assert "/tmp/testdb.json" in out
    assert capsys.readouterr().out == ""


def test_instance_overview_without_a_profile_says_how_to_build_one(monkeypatch):
    """Given no profile, When asked, Then the refusal names census.py."""
    def no_profile():
        raise SystemExit(2)

    monkeypatch.setattr(tools_read.query, "load", no_profile)

    with pytest.raises(ToolExecutionError) as raised:
        tools_read.instance_overview()

    assert "census.py" in str(raised.value)


def test_register_exposes_the_four_read_tools():
    """Given a real MCPServer, When registered, Then all four tools are listed."""
    mcp = MCPServer("test-read-tools")

    tools_read.register(mcp)

    listed = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert listed == {"search_read", "read_record", "count_records", "instance_overview"}
