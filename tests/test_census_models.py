from odoo_assistant.odoo_scripts import census as census_module
from odoo_client import OdooError


def test_has_model_does_not_query_an_absent_model(mock_odoo):
    mock_odoo.set_results("ir.model", 0, method="search_count")

    assert census_module.has_model(mock_odoo, "helpdesk.ticket") is False
    assert mock_odoo.calls == [
        {
            "model": "ir.model",
            "method": "search_count",
            "args": [[["model", "=", "helpdesk.ticket"]]],
            "kwargs": {},
            "domain": [["model", "=", "helpdesk.ticket"]],
        }
    ]


def test_has_model_returns_false_when_existing_model_access_is_refused(mock_odoo):
    mock_odoo.set_results("ir.model", 1, method="search_count")
    mock_odoo.set_results(
        "helpdesk.ticket", OdooError("access denied"), method="search_count"
    )

    assert census_module.has_model(mock_odoo, "helpdesk.ticket") is False
    assert [call["model"] for call in mock_odoo.calls] == [
        "ir.model",
        "helpdesk.ticket",
    ]


def test_has_model_probes_directly_when_the_catalog_is_unreadable(mock_odoo):
    # Measured on Odoo 18 live: a plain Internal User cannot read ir.model at
    # all, and the AccessError killed census for every non-admin. The catalog
    # probe then gives up silently and the model itself decides: readable
    # counts, unreadable or absent does not.
    mock_odoo.set_results(
        "ir.model", OdooError("Se necessario, contattare l'amministratore"),
        method="search_count")

    mock_odoo.set_results("sale.order", 42, method="search_count")
    assert census_module.has_model(mock_odoo, "sale.order") is True

    mock_odoo.set_results(
        "account.move", OdooError("access denied"), method="search_count")
    assert census_module.has_model(mock_odoo, "account.move") is False
    assert [call["model"] for call in mock_odoo.calls] == [
        "ir.model", "sale.order", "ir.model", "account.move",
    ]
