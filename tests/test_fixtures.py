"""The test doubles themselves: do they behave, and do they still mirror reality?

Two kinds of test live here.

`round-trip` proves the doubles are usable: canned results come back out and
every call is recorded.

`anti-drift` proves the doubles stay HONEST. A mock that grows a method the
real class never had turns green tests into fiction — the plan's own example
called `odoo.version()` and `odoo.create()`, neither of which exists on `Odoo`.
Those tests compare the implemented surface against the real class by
reflection, so an invented method or a renamed parameter fails here instead of
misleading every suite built on top (T9-T13).
"""
import inspect

import pytest

from odoo_assistant import server
from odoo_client import Odoo
from write_patterns import Writer

from tests.conftest import MockOdoo, MockWriter


# --------------------------------------------------------------- round-trip

def test_mock_odoo_round_trips_canned_search_read_rows(mock_odoo):
    """Given canned rows, When search_read runs, Then rows and call come back."""
    mock_odoo.set_results("sale.order", [{"id": 1, "name": "SO001"}])

    rows = mock_odoo.search_read(
        "sale.order", [["state", "=", "sale"]], ["name", "state"], limit=10
    )

    assert rows == [{"id": 1, "name": "SO001"}]
    assert mock_odoo.last_call["model"] == "sale.order"
    assert mock_odoo.last_call["method"] == "search_read"
    assert mock_odoo.last_call["domain"] == [["state", "=", "sale"]]
    assert mock_odoo.last_call["kwargs"]["limit"] == 10


def test_mock_odoo_raises_a_programmed_exception(mock_odoo):
    """A canned Exception is raised, not returned — the error paths need this."""
    from odoo_client import OdooExecutedButUnserializable

    mock_odoo.set_results(
        "account.payment", OdooExecutedButUnserializable("posted"), method="action_post"
    )

    with pytest.raises(OdooExecutedButUnserializable):
        mock_odoo.call("account.payment", "action_post", [[7]])


def test_mock_odoo_refuses_private_methods_like_the_real_client(mock_odoo):
    """Odoo rejects `_`-prefixed methods; the double must not be more permissive."""
    from odoo_client import OdooError

    with pytest.raises(OdooError):
        mock_odoo.call("sale.order", "_create_invoices", [[1]])


def test_mock_odoo_is_injected_into_the_server(mock_odoo):
    """The fixture replaces the connection: no socket is ever opened."""
    assert server._get_odoo() is mock_odoo


def test_mock_writer_reuses_the_id_when_unique_on_matches(mock_writer):
    """PATTERN 8: a second create with the same unique_on mints no second id."""
    first = mock_writer.create("res.partner", {"name": "ACME"}, unique_on=["name"])
    second = mock_writer.create("res.partner", {"name": "ACME"}, unique_on=["name"])

    assert first == second
    assert mock_writer.created_ids == {"res.partner": [first]}
    assert mock_writer.log[-1].duplicate_avoided is True


def test_mock_writer_records_the_watch_argument(mock_writer):
    """T12 asserts on gating, so `act` must keep what it was actually asked."""
    mock_writer.set_record("sale.order", 5, {"state": "draft"})
    mock_writer.set_effect("sale.order", "action_confirm", {"state": "sale"})

    result = mock_writer.act("sale.order", "action_confirm", 5, watch="state")

    assert (result.before, result.after) == ("draft", "sale")
    assert result.changed is True
    assert mock_writer.last_call["watch"] == "state"


def test_mock_writer_reports_no_change_when_the_value_is_identical(mock_writer):
    """PATTERN 1: writing the value it already has succeeds and changes nothing."""
    mock_writer.set_record("sale.order", 5, {"client_order_ref": "REF"})

    result = mock_writer.write("sale.order", 5, {"client_order_ref": "REF"})

    assert result.changed is False


# ---------------------------------------------------------------- anti-drift

@pytest.mark.parametrize("invented", ["version", "read", "create"])
def test_mock_odoo_has_no_method_the_real_client_lacks(mock_odoo, invented):
    """`Odoo` has none of these: reads and creates go through `call()`."""
    assert not hasattr(Odoo, invented)

    with pytest.raises(AttributeError):
        getattr(mock_odoo, invented)


def _public_methods(cls):
    return {
        name
        for name, member in vars(cls).items()
        if not name.startswith("_") and inspect.isfunction(member)
    }


def test_mock_odoo_surface_is_a_subset_of_the_real_odoo(mock_odoo):
    """Every doubled method exists on `Odoo` with the very same signature."""
    doubled = _public_methods(MockOdoo) - MockOdoo.PROGRAMMING_API

    assert doubled <= _public_methods(Odoo)
    for name in doubled:
        assert inspect.signature(getattr(MockOdoo, name)) == inspect.signature(
            getattr(Odoo, name)
        ), f"MockOdoo.{name} drifted from Odoo.{name}"


def test_mock_writer_surface_is_a_subset_of_the_real_writer(mock_writer):
    """Same discipline for the Writer: `vals` is not `values`, `ids` is not `id`."""
    doubled = _public_methods(MockWriter) - MockWriter.PROGRAMMING_API

    assert doubled <= _public_methods(Writer)
    for name in doubled:
        assert inspect.signature(getattr(MockWriter, name)) == inspect.signature(
            getattr(Writer, name)
        ), f"MockWriter.{name} drifted from Writer.{name}"
