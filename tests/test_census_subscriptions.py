from odoo_assistant.odoo_scripts import census as census_module


def _run_census(mock_odoo, monkeypatch, sale_fields):
    mock_odoo.set_results(
        "res.company",
        [{"id": 1, "name": "Test Company", "country_id": False}],
    )
    mock_odoo.set_results("ir.module.module", [])
    for model, count in (
        ("ir.ui.menu", 1),
        ("sale.order", 2),
        ("sale.order.line", 3),
        ("product.template", 4),
        ("res.partner", 5),
        ("res.users", 6),
    ):
        mock_odoo.set_results(model, count, method="search_count")
    mock_odoo.set_results("sale.order", [], method="read_group")
    mock_odoo.set_results("account.move", [], method="read_group")
    mock_odoo.set_results("sale.order", sale_fields, method="fields_get")
    mock_odoo.set_results("account.move", {}, method="fields_get")
    mock_odoo.set_results("crm.lead", {}, method="fields_get")
    monkeypatch.setattr(
        census_module, "has_model", lambda _odoo, model: model == "sale.order"
    )

    return census_module.census(mock_odoo)


def test_census_skips_subscriptions_when_sale_order_field_is_absent(
    mock_odoo, monkeypatch
):
    profile = _run_census(mock_odoo, monkeypatch, {})

    assert "subscriptions" not in profile["areas"]
    assert not any(
        leaf[0] == "subscription_state"
        for call in mock_odoo.calls
        for leaf in (call["domain"] or [])
    )


def test_census_reports_subscriptions_when_sale_order_field_is_present(
    mock_odoo, monkeypatch
):
    profile = _run_census(
        mock_odoo, monkeypatch, {"subscription_state": {"type": "selection"}}
    )

    assert profile["areas"]["subscriptions"] == {
        "subscriptions_total_ever": 2,
        "subscriptions_RUNNING": 2,
        "note": "RUNNING = 3_progress + 4_paused. Total includes churned/renewed.",
        "by_state": [],
    }
