"""The read tools: what reaches Odoo, and what comes back.

Every test asserts on the call that was actually recorded, not on the fact that
something was returned — a read tool that quietly drops the company context or
the limit still returns plausible rows.
"""
import asyncio
import json

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
    pytest.param(lambda: tools_read.search_read("sale.order", []), id="search_read"),
    pytest.param(lambda: tools_read.read_record("sale.order", 7), id="read_record"),
    pytest.param(lambda: tools_read.count_records("sale.order"), id="count_records"),
])
def test_missing_credentials_are_mapped_for_every_odoo_read(monkeypatch, read_it):
    from odoo_assistant import server
    from odoo_client import MissingCredentials

    monkeypatch.setattr(
        server, "_get_odoo",
        lambda: (_ for _ in ()).throw(MissingCredentials("missing credentials")),
    )

    with pytest.raises(ToolExecutionError) as failure:
        read_it()

    assert "nothing was sent to Odoo" in str(failure.value)


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


def test_instance_overview_reports_the_profile_without_printing(
    monkeypatch, capsys, tmp_path
):
    """Given a profile already cached for the connected instance, When asked,
    Then the report is returned rather than printed — stdout belongs to the
    JSON-RPC stream — and the instance is not re-censused."""
    monkeypatch.setattr(tools_read.census, "PROFILE_DIR", str(tmp_path))
    monkeypatch.setattr(tools_read.query, "PROFILE_DIR", str(tmp_path))
    (tmp_path / "testdb.json").write_text(json.dumps(PROFILE))

    class _Connected:
        db = "testdb"
        base = "https://testdb.example.com"

    def _must_not_run(odoo):
        raise AssertionError("a cached profile must not trigger a new census")

    monkeypatch.setattr(tools_read.server, "_get_odoo", lambda: _Connected())
    monkeypatch.setattr(tools_read.census, "census", _must_not_run)

    out = tools_read.instance_overview()

    assert "Odoo 18.0 enterprise" in out
    assert "[2] Alpha S.L. (Spain)" in out
    assert "invoices_OUT=377" in out
    assert "testdb.json" in out
    assert capsys.readouterr().out == ""


def test_overview_elides_area_fields_only_at_field_boundaries(capsys):
    """Given a wide area, When rendered, Then omitted fields are explicit."""
    profile = {
        "fingerprint": {
            "odoo_version": "16.0", "edition": "enterprise",
            "module_count": 285, "transport": "xmlrpc",
            "taken_at": "2026-08-13T10:00:00",
        },
        "companies": [],
        "areas": {
            "accounting": {
                "move_types": {
                    "out_invoice": 14857, "in_invoice": 11331,
                    "out_refund": 573, "in_refund": 498, "entry": 93532,
                },
                "invoiced_total_company_currency": 22385318.32,
                "outstanding_receivable": 168775.73,
                "payments": 8771,
                "journals": 36,
            },
        },
    }

    tools_read.query.overview(profile)

    line = next(line for line in capsys.readouterr().out.splitlines()
                if line.startswith("  accounting"))
    summary = line.split(maxsplit=1)[1]
    assert summary.endswith("; …")
    assert len(summary) <= 112
    assert "invoiced_total_company_currency=22385318.32" in summary
    assert all("=" in field for field in summary.removesuffix("; …").split("; "))
    assert not summary.endswith((
        "invoiced_total_company_currency", "outstanding_receivable",
        "payments", "journals",
    ))


def test_instance_overview_refresh_rebuilds_a_stale_profile(monkeypatch, tmp_path):
    """Given a cached profile that no longer matches the instance, When refresh
    is asked for, Then the profile is rebuilt from Odoo and the new figures are
    the ones reported — a snapshot served as current is the silent-wrong-answer
    this whole tool exists to avoid."""
    monkeypatch.setattr(tools_read.census, "PROFILE_DIR", str(tmp_path))
    monkeypatch.setattr(tools_read.query, "PROFILE_DIR", str(tmp_path))
    (tmp_path / "testdb.json").write_text(json.dumps(_profile_named("Stale S.L.")))

    class _Connected:
        db = "testdb"
        base = "https://testdb.example.com"

    monkeypatch.setattr(tools_read.server, "_get_odoo", lambda: _Connected())
    monkeypatch.setattr(
        tools_read.census, "census", lambda odoo: _profile_named("Current S.L."))

    out = tools_read.instance_overview(refresh=True)

    assert "Current S.L." in out
    assert "Stale S.L." not in out
    assert "Current S.L." in (tmp_path / "testdb.json").read_text()


def test_required_fields_prints_the_default_beside_how_records_really_use_it(mock_odoo):
    """Given a required selection with a default, When asked, Then the live split shows.

    Odoo defaults `crm.lead.type` to 'lead'. On an instance that runs its
    pipeline as opportunities, a create that accepts that default lands in a
    menu nobody opens, and nothing errors — so the default alone is not enough
    to report.
    """
    mock_odoo.set_results("crm.lead", {
        "name": {"string": "Opportunity", "type": "char", "required": True},
        "type": {"string": "Type", "type": "selection", "required": True,
                 "selection": [["lead", "Lead"], ["opportunity", "Opportunity"]]},
        "email_from": {"string": "Email", "type": "char", "required": False},
    }, method="fields_get")
    mock_odoo.set_results("crm.lead", {"type": "lead"}, method="default_get")
    mock_odoo.set_results("crm.lead", [
        {"type": "lead", "__count": 1},
        {"type": "opportunity", "__count": 12},
    ], method="read_group")

    out = tools_read.required_fields("crm.lead")

    assert "requires 2 field(s)" in out
    assert "one of: lead | opportunity" in out
    assert "default to 'lead'" in out
    assert "lead=1, opportunity=12" in out
    assert "email_from" not in out


def test_required_fields_reports_a_guard_refusal_instead_of_an_empty_history(mock_odoo):
    """Given account.move, When asked, Then the refused count says so.

    An unfiltered `read_group` on that table is exactly what the structural
    guard exists to stop, and "no records yet" would be a lie about a model
    holding thousands.
    """
    mock_odoo.set_results("account.move", {
        "move_type": {"string": "Type", "type": "selection", "required": True,
                      "selection": [["entry", "Journal Entry"],
                                    ["out_invoice", "Customer Invoice"]]},
    }, method="fields_get")
    mock_odoo.set_results("account.move", {"move_type": "entry"}, method="default_get")

    out = tools_read.required_fields("account.move")

    assert "default to 'entry'" in out
    assert "structural guard" in out
    assert "no records yet" not in out


def test_the_phone_note_only_appears_where_odoo_computes_phone_sanitized(mock_odoo):
    """Given a model without the phone mixin, When asked, Then no phone advice."""
    mock_odoo.set_results("project.task", {
        "name": {"string": "Title", "type": "char", "required": True},
    }, method="fields_get")
    mock_odoo.set_results("project.task", {}, method="default_get")

    out = tools_read.required_fields("project.task")

    assert "phone_sanitized" not in out
    assert "E.164" not in out


def test_register_exposes_the_read_tools():
    """Given a real MCPServer, When registered, Then every read tool is listed."""
    mcp = MCPServer("test-read-tools")

    tools_read.register(mcp)

    listed = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert listed == {
        "search_read", "read_record", "count_records", "instance_overview",
        "required_fields",
    }


def _profile_named(company: str) -> dict:
    """A minimal profile `query.overview` can render, tagged by company."""
    return {
        "fingerprint": {
            "odoo_version": "18.0", "edition": "enterprise",
            "module_count": 285, "transport": "xmlrpc",
            "taken_at": "2026-08-13T10:00:00",
        },
        "companies": [{"id": 2, "name": company, "country_id": [68, "Spain"]}],
        "areas": {"accounting": {"invoices_OUT": 1}},
        "anomalies": {},
    }


def test_instance_overview_profiles_the_connected_instance_not_a_neighbour(
    monkeypatch, tmp_path
):
    """Given another instance already profiled in the shared cache directory,
    When the overview is asked while connected to a DIFFERENT instance, Then it
    reports the connected one — never the neighbour's numbers.

    The profile is chosen from the live connection, not from an environment
    variable: with ODOO_DB unset (it is discovered now) the old lookup fell
    back to the first file on disk and reported another instance as this one,
    with no error and a perfectly plausible report.
    """
    monkeypatch.setattr(tools_read.census, "PROFILE_DIR", str(tmp_path))
    monkeypatch.setattr(tools_read.query, "PROFILE_DIR", str(tmp_path))
    monkeypatch.delenv("ODOO_DB", raising=False)
    (tmp_path / "beta.json").write_text(json.dumps(_profile_named("Beta S.L.")))

    class _Connected:
        db = "alpha"
        base = "https://alpha.example.com"

    monkeypatch.setattr(tools_read.server, "_get_odoo", lambda: _Connected())
    monkeypatch.setattr(
        tools_read.census, "census", lambda odoo: _profile_named("Alpha S.L."))

    out = tools_read.instance_overview()

    assert "Alpha S.L." in out, f"did not report the connected instance: {out}"
    assert "Beta S.L." not in out, f"served a neighbour's profile: {out}"
    assert (tmp_path / "alpha.json").is_file(), "the built profile was not cached"
