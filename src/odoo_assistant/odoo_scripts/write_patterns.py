#!/usr/bin/env python3
"""Writing to Odoo: the patterns, not the recipes. Stdlib only.

Every pattern here was discovered by writing to a real instance and watching
what happened — not from documentation.

    from write_patterns import Writer
    w = Writer(odoo, company_id=2)
    r = w.act("sale.order", "action_confirm", [sid], watch="state")
    print(r.before, "->", r.after, "changed:", r.changed)

Six patterns, in the order they bite:

  1. VERIFY THE EFFECT, never the return value
  2. An exception does NOT mean nothing happened
  3. Every call is its own transaction — there is no rollback across calls
  4. Multi-model operations go through a wizard, never a private method
  5. The company comes from the record, not from the context
  6. State transitions are one-way and not idempotent
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from odoo_client import (connect_cli as connect, OdooError,  # noqa: E402
                         OdooExecutedButUnserializable)


class WriteResult:
    """What actually happened, as opposed to what the call returned."""

    def __init__(self, model, method, ids, before, after, watch,
                 raised=None, returned=None):
        self.model, self.method, self.ids = model, method, ids
        self.before, self.after, self.watch = before, after, watch
        self.raised, self.returned = raised, returned

    @property
    def changed(self):
        return self.before != self.after

    @property
    def ok(self):
        """PATTERN 1: success is a state change, not the absence of an
        exception. A call can return cleanly and change nothing (write with
        an identical value), or raise and have changed everything (see
        OdooExecutedButUnserializable)."""
        return self.changed

    def __repr__(self):
        arrow = f"{self.before!r} -> {self.after!r}"
        flag = "CHANGED" if self.changed else "NO CHANGE"
        warn = "  [raised but executed]" if self.raised else ""
        return f"<{self.model}.{self.method} {self.watch}: {arrow} {flag}{warn}>"


class Writer:
    """Writes that report their real effect."""

    def __init__(self, odoo, company_id=None):
        self.o = odoo
        self.company_id = company_id
        self.ctx = {"allowed_company_ids": [company_id]} if company_id else {}
        self.log = []

    # ---------------------------------------------------------------- reads
    def _read(self, model, ids, field):
        try:
            rows = self.o.call(model, "read", [ids], {"fields": [field],
                                                      "context": self.ctx})
            vals = [r.get(field) for r in rows]
            return vals[0] if len(vals) == 1 else vals
        except OdooError:
            return None

    def state_of(self, model, ids, fields=None):
        """Read the state fields that matter, and never fail silently.

        PATTERN 12: ALWAYS name the fields you want.

        A cold-start run could not read an invoice it had just created and
        reported "state presumibile: draft". The invoice was `posted`, number
        FT/2026/0062. The read had failed because it asked for every field —
        on account.move that is 200+ columns, including binary and computed
        ones that can blow up serialisation.

        Asking for three named fields always works. Never guess a state:
        if the read fails, say the read failed, not what you assume.
        """
        ids = ids if isinstance(ids, list) else [ids]
        fields = fields or ["name", "state", "payment_state", "amount_residual"]
        available = self.o.fields_get(model, [], ["type"])
        fields = [f for f in fields if f in available]
        rows = self.o.call(model, "search_read", [[["id", "in", ids]]],
                           {"fields": fields, "context": self.ctx})
        return rows[0] if len(rows) == 1 else rows

    # --------------------------------------------------------------- create
    def create(self, model, vals, verify="id", unique_on=None):
        """PATTERN 5: put company_id in the VALUES.

        `allowed_company_ids` in the context controls what you can SEE, not
        which company a new record belongs to. A sales order created without
        it landed on the company that has no sales journal, and only failed
        three steps later at invoicing with a message about missing journals.

        PATTERN 8: check for an existing record first.

        A cold-start test asked for one customer and produced FOUR identical
        ones (ids 1444-1447): the agent retried after calls it believed had
        failed. Retrying a create is never safe — Odoo has no idempotency
        key. Pass `unique_on` to make it safe:

            w.create("res.partner", {...}, unique_on=["name", "email"])

        Returns the existing id instead of creating a duplicate.
        """
        if unique_on:
            dom = [[f, "=", vals[f]] for f in unique_on if f in vals]
            if dom:
                found = self.o.call(model, "search", [dom],
                                    {"limit": 1, "context": self.ctx})
                if found:
                    existing = found[0]
                    r = WriteResult(model, "create", [existing], existing,
                                    existing, verify)
                    r.duplicate_avoided = True
                    self.log.append(r)
                    return existing

        if self.company_id and "company_id" not in vals:
            fields = self.o.fields_get(model, ["company_id"], ["type"])
            if "company_id" in fields:
                vals = dict(vals, company_id=self.company_id)

        res = self.o.call(model, "create", [vals], {"context": self.ctx})
        # create returns a list on /json/2 and an int on XML-RPC
        ids = res if isinstance(res, list) else [res]

        # PATTERN 1 applied to create: confirm the record is really there.
        exists = self.o.call(model, "search_count", [[["id", "in", ids]]],
                             {"context": self.ctx})
        if not exists:
            raise OdooError(
                f"{model}.create returned {res} but no such record exists. "
                f"Do not report this as created.")

        r = WriteResult(model, "create", ids, None, ids[0], verify, returned=res)
        self.log.append(r)
        return ids[0]

    # ---------------------------------------------------------------- write
    def write(self, model, ids, vals, watch=None):
        """PATTERN 1: writing the value it already has succeeds and changes
        nothing. Only a before/after comparison distinguishes the two."""
        ids = ids if isinstance(ids, list) else [ids]
        watch = watch or next(iter(vals))
        before = self._read(model, ids, watch)
        raised = None
        try:
            self.o.call(model, "write", [ids, vals], {"context": self.ctx})
        except OdooExecutedButUnserializable as e:
            raised = str(e)
        after = self._read(model, ids, watch)
        r = WriteResult(model, "write", ids, before, after, watch, raised)
        self.log.append(r)
        return r

    # ----------------------------------------------------------------- act
    def act(self, model, method, ids, watch="state", args=None, context=None):
        """Call an action method and report what it changed.

        PATTERN 2: an exception does not mean nothing happened.
        `account.payment.action_post` raises `cannot marshal None` from
        Odoo's own serialiser — after posting the payment. Retrying would
        post it twice. So: catch, re-read, decide from the state.

        PATTERN 6: transitions are one-way. Calling `action_confirm` on an
        already-confirmed order raises "not in a state that requires
        confirmation". Check the state first; do not use exceptions as
        control flow.
        """
        ids = ids if isinstance(ids, list) else [ids]
        ctx = dict(self.ctx, **(context or {}))
        before = self._read(model, ids, watch)
        raised = returned = None
        try:
            returned = self.o.call(model, method, [ids] + (args or []),
                                   {"context": ctx})
        except OdooExecutedButUnserializable as e:
            raised = str(e)          # ran, could not serialise — NOT a failure

        # PATTERN 11: a dict with res_model is not a result, it is "open this
        # wizard". `action_cancel` returns one and leaves the order confirmed.
        # Follow it, or the caller will believe the work is done.
        if isinstance(returned, dict) and returned.get("res_model") and \
                returned.get("view_mode") == "form":
            wm = returned["res_model"]
            wctx = dict(ctx, active_model=model, active_ids=ids, active_id=ids[0],
                        **(returned.get("context") or {}))
            try:
                wid = self.o.call(wm, "create", [{}], {"context": wctx})
                wid = wid[0] if isinstance(wid, list) else wid
                for m in (method, "action_confirm", "confirm", "action_apply"):
                    try:
                        self.o.call(wm, m, [[wid]], {"context": wctx})
                        break
                    except OdooError:
                        continue
            except OdooError:
                pass                  # leave before/after to tell the truth

        after = self._read(model, ids, watch)
        r = WriteResult(model, method, ids, before, after, watch, raised, returned)
        self.log.append(r)
        return r

    # --------------------------------------------------------------- wizard
    def wizard(self, wizard_model, method, on_model, on_ids, vals=None,
               extra_context=None):
        """PATTERN 4: multi-model operations go through a wizard.

        Private methods (`_create_invoices`) are refused by Odoo outright.
        The public path is always the same three steps, and the wizard reads
        most of its own defaults from the context:

            context = {active_model, active_ids, active_id}
            wid = wizard.create(vals)
            wizard.<method>([wid])

        Forgetting active_model/active_ids is the usual cause of a wizard
        that "runs" but produces nothing.
        """
        on_ids = on_ids if isinstance(on_ids, list) else [on_ids]
        ctx = dict(self.ctx,
                   active_model=on_model, active_ids=on_ids, active_id=on_ids[0],
                   **(extra_context or {}))
        wid = self.o.call(wizard_model, "create", [vals or {}], {"context": ctx})
        wid = wid[0] if isinstance(wid, list) else wid
        raised = returned = None
        try:
            returned = self.o.call(wizard_model, method, [[wid]], {"context": ctx})
        except OdooExecutedButUnserializable as e:
            raised = str(e)
        r = WriteResult(wizard_model, method, [wid], None, None, "wizard",
                        raised, returned)
        self.log.append(r)
        return r

    # ----------------------------------------------------------- inspection
    def can(self, model, method, ids):
        """PATTERN 6: ask before acting.

        Returns (allowed, reason). Reads the current state and compares it to
        what the transition needs, instead of calling and catching."""
        ids = ids if isinstance(ids, list) else [ids]
        state = self._read(model, ids, "state")
        rules = {
            "action_confirm": ({"draft", "sent"}, "already confirmed"),
            "action_post": ({"draft"}, "already posted"),
            "action_cancel": ({"draft", "sent", "sale", "posted"}, "already cancelled"),
            "unlink": ({"draft", "cancel"}, "posted/confirmed records cannot be deleted"),
        }
        if method not in rules:
            return True, "no known precondition"
        allowed, why = rules[method]
        states = state if isinstance(state, list) else [state]
        bad = [s for s in states if s not in allowed]
        if bad:
            return False, f"state={bad[0]}: {why}"
        return True, f"state={states[0]}"

    def step(self, name, check_domain, check_model, do, expected=1):
        """PATTERN 10: make a whole STEP idempotent, not just a create.

        `unique_on` stops duplicate partners. It does not stop this:

            run asks for one order -> agent creates S00428, believes it
            failed, creates S00429, then S00430. Three orders, two orphan
            draft invoices.

        The retry happened one level up, around the whole step. So guard the
        step itself: if the expected result already exists, skip the work.

            w.step("order for ACME", [["partner_id","=",pid]], "sale.order",
                   lambda: w.create("sale.order", {...}))

        Returns (ids, created) — `created` is False when it was already there.
        """
        found = self.o.call(check_model, "search", [check_domain],
                            {"context": self.ctx})
        if len(found) >= expected:
            r = WriteResult(check_model, f"step:{name}", found, found[0],
                            found[0], "step")
            r.duplicate_avoided = True
            self.log.append(r)
            return found, False

        do()

        after = self.o.call(check_model, "search", [check_domain],
                            {"context": self.ctx})
        if len(after) <= len(found):
            raise OdooError(
                f"step '{name}' ran but produced nothing "
                f"({len(found)} -> {len(after)}). Do not retry blindly: "
                f"find out why before calling it again.")
        return after, True

    def summary(self):
        lines = ["", "What actually changed:"]
        for r in self.log:
            extra = "  [existing record reused]" if getattr(
                r, "duplicate_avoided", False) else ""
            lines.append("  " + repr(r) + extra)
        no_effect = [r for r in self.log if r.watch != "wizard" and not r.changed
                     and not getattr(r, "duplicate_avoided", False)]
        if no_effect:
            lines.append("")
            lines.append(f"  {len(no_effect)} call(s) changed nothing — "
                         "do not report those as done.")
        return "\n".join(lines)

    def report(self, steps):
        """PATTERN 9: report per requested step, from re-read state.

        A cold-start test claimed "all 8 tasks completed" while having
        created 4 duplicate customers, written no internal note, and quoted
        an invoice belonging to someone else's earlier test. The agent
        described its intentions, not the database.

        Pass what was ASKED and how to check it. Anything that cannot be
        proven by a query is reported as NOT DONE.

            w.report([
                ("create customer", "res.partner",
                 [["name", "=", "ACME"]], 1),
                ("internal note", "res.partner",
                 [["name", "=", "ACME"], ["comment", "!=", False]], 1),
            ])
        """
        out = ["", "Verified against the database:", ""]
        all_ok = True
        for label, model, domain, expected in steps:
            try:
                n = self.o.call(model, "search_count", [domain],
                                {"context": self.ctx})
            except OdooError as e:
                out.append(f"  ?  {label}: could not verify ({str(e)[:50]})")
                all_ok = False
                continue
            if expected is None:
                ok = n > 0
                want = "at least 1"
            else:
                ok = n == expected
                want = str(expected)
            mark = "OK  " if ok else "NOT DONE"
            out.append(f"  {mark} {label}: found {n}, expected {want}")
            if n > (expected or 1):
                out.append(f"       WARNING: {n} records match — "
                           f"possible duplicates from retrying")
            all_ok = all_ok and ok
        out.append("")
        out.append("  All requested steps verified." if all_ok else
                   "  NOT everything was done. Report only the OK lines as done.")
        return "\n".join(out)


if __name__ == "__main__":
    o = connect()
    comp = None
    for c in o.search_read("res.company", [], ["id", "name"]):
        if o.search_count("account.journal",
                          [["type", "=", "sale"], ["company_id", "=", c["id"]]]):
            comp = c
            break
    print(f"Using company {comp['id']} ({comp['name']}) — it has a sales journal\n")

    w = Writer(o, company_id=comp["id"])
    partner = o.search_read("res.partner", [["customer_rank", ">", 0]],
                            ["id", "name"], limit=1, context=w.ctx)[0]
    prod = o.search_read("product.product",
                         [["sale_ok", "=", True], ["invoice_policy", "=", "order"]],
                         ["id", "name"], limit=1, context=w.ctx)[0]

    sid = w.create("sale.order", {
        "partner_id": partner["id"],
        "order_line": [[0, 0, {"product_id": prod["id"], "product_uom_qty": 1}]],
    })
    print(f"created sale.order {sid}")

    ok, why = w.can("sale.order", "action_confirm", sid)
    print(f"can confirm? {ok} ({why})")
    print(w.act("sale.order", "action_confirm", sid))

    ok, why = w.can("sale.order", "action_confirm", sid)
    print(f"can confirm again? {ok} ({why})   <- ask, do not retry blindly")

    ok, why = w.can("sale.order", "unlink", sid)
    print(f"can delete? {ok} ({why})")

    print(w.write("sale.order", sid, {"client_order_ref": "PATTERN-TEST"}))
    print(w.write("sale.order", sid, {"client_order_ref": "PATTERN-TEST"}),
          "  <- same value: no change")

    print(w.summary())
