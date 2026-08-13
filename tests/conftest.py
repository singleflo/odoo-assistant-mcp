"""Test doubles for the boundary WE own: the `Odoo` client and the `Writer`.

Nothing below fakes `xmlrpc.client` or `urllib` — mocking a transport we do not
own would only prove that our idea of the wire matches itself. These doubles
stand exactly where the real classes stand, with the real method names and the
real signatures, and `tests/test_fixtures.py` enforces that by reflection.

What the real surface actually is (read from the source, not assumed):

    Odoo    call, search_count, search_read, read_group, fields_get, info
            — there is NO `read()`, NO `create()` and NO `version()`. Reads and
              creates go through `call(model, "read"|"create", ...)`, and the
              server release comes from `info()["odoo_version"]`.
    Writer  create, write, act, wizard, can, step, state_of, summary, report
            — `create(model, vals, ...)`, not `values`; `state_of(model, ids)`,
              not `id`.

Usage:

    def test_something(mock_odoo):
        mock_odoo.set_results("sale.order", [{"id": 1, "name": "SO001"}])
        ...
        assert mock_odoo.last_call["domain"] == [["state", "=", "sale"]]
"""
import pytest

# Importing the server first runs its bootstrap (`sys.path.insert` on
# odoo_scripts/), which is what makes the flat modules below importable here.
from odoo_assistant import server  # noqa: F401  (imported for its side effect)

from odoo_client import OdooError  # noqa: E402  (needs the bootstrap above)
from write_patterns import WriteResult  # noqa: E402


class MockOdoo:
    """A programmable `Odoo`: canned results in, recorded calls out.

    Results are keyed by (model, method) and set with `set_results`. A result
    that IS an exception is raised instead of returned — that is how the
    `OdooExecutedButUnserializable` path gets exercised without a live server.

    An unprogrammed call fails loudly rather than returning a plausible empty
    value: a test that never said what Odoo answers is not a passing test.
    """

    PROGRAMMING_API = frozenset({"set_results", "last_call"})

    def __init__(self):
        self.results = {}
        self.calls = []
        self.instance_info = {
            "base_url": "http://odoo.invalid:8069",
            "database": "testdb",
            "uid": 2,
            "transport": "xmlrpc",
            "odoo_version": "18.0",
            "edition": "enterprise",
        }

    # ---------------------------------------------------------- programming
    def set_results(self, model, result, method="search_read"):
        """Program what `model.method` answers. An Exception is raised, not returned."""
        self.results[(model, method)] = result

    @property
    def last_call(self):
        return self.calls[-1] if self.calls else None

    # ------------------------------------------------------- doubled surface
    def call(self, model, method, args=None, kwargs=None):
        args = args if args is not None else []
        kwargs = kwargs or {}
        if method.startswith("_"):
            raise OdooError(
                f"{model}.{method}: private methods are always rejected by "
                "Odoo (check_method_name). Use the public wizard instead."
            )
        self.calls.append({
            "model": model,
            "method": method,
            "args": args,
            "kwargs": kwargs,
            "domain": args[0] if args else None,
        })
        if (model, method) not in self.results:
            raise AssertionError(
                f"MockOdoo was asked for {model}.{method} but nothing was "
                f"programmed. Add: set_results({model!r}, <result>, "
                f"method={method!r})"
            )
        result = self.results[(model, method)]
        if isinstance(result, Exception):
            raise result
        return result

    def search_count(self, model, domain, context=None):
        return self.call(model, "search_count", [domain],
                         {"context": context} if context else {})

    def search_read(self, model, domain, fields, limit=None, order=None, context=None):
        kw = {"fields": fields}
        if limit:
            kw["limit"] = limit
        if order:
            kw["order"] = order
        if context:
            kw["context"] = context
        return self.call(model, "search_read", [domain], kw)

    def read_group(self, model, domain, fields, groupby, context=None):
        kw = {"context": context} if context else {}
        return self.call(model, "read_group", [domain, fields, groupby], kw)

    def fields_get(self, model, fields=None, attributes=None):
        return self.call(model, "fields_get", [fields or []],
                         {"attributes": attributes or ["string", "type", "selection"]})

    def info(self):
        self.calls.append({"model": None, "method": "info", "args": [],
                           "kwargs": {}, "domain": None})
        return self.instance_info


class MockWriter:
    """A programmable `Writer` that keeps the patterns the real one enforces.

    It holds a tiny record store so before/after comparison is real: `write`
    applies the values, `act` applies whatever `set_effect` declared, and an
    action nobody programmed changes nothing — which is exactly how a wrong
    method behaves against a live instance.

    Two deliberate simplifications, so no test reads more into it than it says:
    `step` does not evaluate `check_domain` (it counts seeded records for the
    model), and `report` does not count anything — it records the steps it was
    asked to verify.
    """

    PROGRAMMING_API = frozenset({"set_record", "set_effect", "last_call"})

    def __init__(self, odoo, company_id=None):
        self.o = odoo
        self.company_id = company_id
        self.ctx = {"allowed_company_ids": [company_id]} if company_id else {}
        self.log = []
        self.calls = []
        self.records = {}
        self.effects = {}
        self.created_ids = {}
        self._unique = {}
        self._next_id = 1

    # ---------------------------------------------------------- programming
    def set_record(self, model, rec_id, values):
        """Seed a record so `state_of`, `write` and `act` have a before-state."""
        self.records[(model, rec_id)] = dict(values)

    def set_effect(self, model, method, values):
        """Declare what calling `model.method` through `act` changes."""
        self.effects[(model, method)] = dict(values)

    @property
    def last_call(self):
        return self.calls[-1] if self.calls else None

    def _record(self, call, **arguments):
        self.calls.append(dict(arguments, call=call))

    def _value(self, model, ids, field):
        values = [self.records.get((model, i), {}).get(field) for i in ids]
        return values[0] if len(values) == 1 else values

    # ------------------------------------------------------- doubled surface
    def create(self, model, vals, verify="id", unique_on=None):
        self._record("create", model=model, vals=vals, verify=verify,
                     unique_on=unique_on)
        key = ((model, tuple((f, vals[f]) for f in unique_on if f in vals))
               if unique_on else None)
        if key is not None and key in self._unique:
            existing = self._unique[key]
            result = WriteResult(model, "create", [existing], existing, existing,
                                 verify)
            result.duplicate_avoided = True
            self.log.append(result)
            return existing

        rec_id = self._next_id
        self._next_id += 1
        self.records[(model, rec_id)] = dict(vals)
        self.created_ids.setdefault(model, []).append(rec_id)
        if key is not None:
            self._unique[key] = rec_id
        self.log.append(WriteResult(model, "create", [rec_id], None, rec_id, verify))
        return rec_id

    def write(self, model, ids, vals, watch=None):
        ids = ids if isinstance(ids, list) else [ids]
        watch = watch or next(iter(vals))
        self._record("write", model=model, ids=ids, vals=vals, watch=watch)
        before = self._value(model, ids, watch)
        for rec_id in ids:
            self.records.setdefault((model, rec_id), {}).update(vals)
        after = self._value(model, ids, watch)
        result = WriteResult(model, "write", ids, before, after, watch)
        self.log.append(result)
        return result

    def act(self, model, method, ids, watch="state", args=None, context=None):
        ids = ids if isinstance(ids, list) else [ids]
        ctx = dict(self.ctx, **(context or {}))
        self._record("act", model=model, method=method, ids=ids, watch=watch,
                     args=args, context=ctx)
        before = self._value(model, ids, watch)
        for rec_id in ids:
            self.records.setdefault((model, rec_id), {}).update(
                self.effects.get((model, method), {}))
        after = self._value(model, ids, watch)
        result = WriteResult(model, method, ids, before, after, watch)
        self.log.append(result)
        return result

    def state_of(self, model, ids, fields=None):
        ids = ids if isinstance(ids, list) else [ids]
        self._record("state_of", model=model, ids=ids, fields=fields)
        rows = [dict(self.records.get((model, i), {}), id=i) for i in ids]
        if fields:
            rows = [{k: v for k, v in row.items() if k in fields or k == "id"}
                    for row in rows]
        return rows[0] if len(rows) == 1 else rows

    def step(self, name, check_domain, check_model, do, expected=1):
        self._record("step", name=name, check_domain=check_domain,
                     check_model=check_model, expected=expected)
        found = [i for (m, i) in self.records if m == check_model]
        if len(found) >= expected:
            result = WriteResult(check_model, f"step:{name}", found, found[0],
                                 found[0], "step")
            result.duplicate_avoided = True
            self.log.append(result)
            return found, False

        do()

        after = [i for (m, i) in self.records if m == check_model]
        if len(after) <= len(found):
            raise OdooError(
                f"step '{name}' ran but produced nothing "
                f"({len(found)} -> {len(after)}). Do not retry blindly: "
                f"find out why before calling it again.")
        return after, True

    def report(self, steps):
        self._record("report", steps=steps)
        return "\n".join(f"  {label}" for label, _model, _domain, _expected in steps)


@pytest.fixture
def mock_odoo(monkeypatch):
    """Given: the server hands out a programmable double instead of connecting.

    Same mechanism as `test_connection.py::clean_environment` — the cached
    singleton is cleared first, so no earlier test can leak a client in.
    """
    odoo = MockOdoo()
    monkeypatch.setattr(server, "_odoo_instance", None)
    monkeypatch.setattr(server, "_get_odoo", lambda: odoo)
    return odoo


@pytest.fixture
def mock_writer(mock_odoo):
    """A `Writer` double bound to the same injected client."""
    return MockWriter(mock_odoo)
