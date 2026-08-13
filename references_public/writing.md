# Writing to Odoo — patterns, not recipes

Every pattern below was found by writing to a live Odoo instance and watching what
happened. The commands that produced each finding are shown so you can check
them on yours.

Use `scripts/write_patterns.py`, which implements all seven:

```python
import sys; sys.path.insert(0, "scripts")
from odoo_client import connect
from write_patterns import Writer

w = Writer(connect(), company_id=target_company_id)
r = w.act("sale.order", "action_confirm", sid, watch="state")
print(r)            # <sale.order.action_confirm state: 'draft' -> 'sale' CHANGED>
```

---

## 1. Success is a state change, not the absence of an exception

The return value of an Odoo write tells you almost nothing. A call can
succeed and change nothing:

```
<sale.order.write client_order_ref: False -> 'REF-1'   CHANGED>
<sale.order.write client_order_ref: 'REF-1' -> 'REF-1' NO CHANGE>
```

Both calls returned `True`. Only the second was a no-op. **Read the field
before, read it after, compare.** If they are equal, the action did not take
effect — do not report it as done.

This is the pattern the other six are variations of.

---

## 2. An exception does NOT mean nothing happened

Odoo's XML-RPC endpoint serialises the return value with `dumps()` and no
`allow_none`. A method that returns `None` therefore **raises after doing its
work**:

```
account.payment.action_post
  -> Fault: cannot marshal None unless allow_none is enabled
  -> payment state afterwards: in_process     # it posted
```

Observed again on the invoicing wizard: `create_invoices` raised, and an
invoice existed. **Retrying would have created a second invoice.**

`odoo_client.py` raises `OdooExecutedButUnserializable` for this case
specifically. The only correct response:

```python
try:
    w.act("account.move", "action_post", iid)
except OdooExecutedButUnserializable:
    pass                      # do NOT retry
state = odoo.search_read("account.move", [["id", "=", iid]], ["state"])
```

`Writer.act()` does this for you and reports the before/after.

---

## 3. Every call is its own transaction — no rollback across calls

Measured:

| Scenario | Result |
|---|---|
| `create([{ok}, {ok}, {broken}])` — one call | **0 records created** — the call is atomic |
| `create(...)` then `write(...)` that fails | **the created record stays** |

There is no `BEGIN`/`ROLLBACK` spanning several API calls. A chain that dies
halfway leaves everything before the failure committed.

So for multi-step work:

- Do the reversible steps first, the irreversible ones last.
- After each step, verify before starting the next.
- Know the compensating action for each step *before* you start:

| Step | Undo |
|---|---|
| `create` | `unlink` (while still draft) |
| `action_confirm` | `action_cancel` |
| `action_post` on an invoice | **none** — only a credit note (`account.move.reversal`) |
| payment registered | `action_draft` then `unlink`, if not reconciled |

---

## 4. Multi-model operations go through a wizard

Private methods are refused outright:

```
sale.order._create_invoices
  -> private methods are always rejected by Odoo (check_method_name)
```

The public path is always the same three steps:

```python
ctx = {"active_model": "sale.order", "active_ids": [sid], "active_id": sid}
wid = odoo.call("sale.advance.payment.inv", "create", [vals], {"context": ctx})
odoo.call("sale.advance.payment.inv", "create_invoices", [[wid]], {"context": ctx})
```

**The context is not optional.** The wizard reads its defaults from
`active_model` / `active_ids`; without them it runs and produces nothing.

Common wizard paths:

| Goal | Wizard | Method |
|---|---|---|
| invoice a sales order | `sale.advance.payment.inv` | `create_invoices` |
| register a payment | `account.payment.register` | `action_create_payments` |
| credit note | `account.move.reversal` | `reverse_moves` |
| cancel an order | `sale.order.cancel` | (via `action_cancel`) |

`Writer.wizard()` wraps all of this.

---

## 5. The company comes from the record, not the context

`allowed_company_ids` controls what you can **see**. It does not decide which
company a new record belongs to.

A sales order created without an explicit `company_id` landed on the company
that has no sales journal. It confirmed fine. It failed three steps later:

```
No suitable sales journal exists for the selected company.
```

The error names invoicing, but the mistake happened at creation. Always:

```python
odoo.call("sale.order", "create", [{..., "company_id": 2}])
```

And check first which companies can actually invoice:

```python
for c in odoo.search_read("res.company", [], ["id", "name"]):
    n = odoo.search_count("account.journal",
                          [["type", "=", "sale"], ["company_id", "=", c["id"]]])
    # target company -> a sale journal exists
    # another company -> no sale journal: cannot invoice
```

---

## 6. Transitions are one-way and not idempotent

Calling an action twice is an error, not a no-op:

```
action_confirm on a confirmed order
  -> "Alcuni ordini non si trovano in uno stato che richiede una conferma"

unlink on a confirmed order
  -> "Impossibile eliminare ... Devono prima essere annullati"
```

So **ask before acting** instead of catching exceptions:

```python
ok, why = w.can("sale.order", "action_confirm", sid)
# False, "state=sale: already confirmed"
```

Known preconditions:

| Method | Requires state |
|---|---|
| `action_confirm` | `draft`, `sent` |
| `action_post` | `draft` |
| `unlink` | `draft`, `cancel` |
| `action_cancel` | anything not already cancelled |

---

## 7. `in_payment` is not `paid`

After registering a payment:

```
payment_state = in_payment      amount_residual = <example amount> (unchanged)
```

The full selection:

```
not_paid · in_payment · paid · partial · reversed · blocked · invoicing_legacy
```

`in_payment` means the payment is recorded but **not reconciled with the bank
statement** — the receivable is still open. The related `account.payment` has
`is_matched = False`.

Telling a user the invoice is "paid" at this point is wrong. It is *in course
of payment*. Only bank reconciliation moves it to `paid` and takes the
residual to zero.

---

## 8. Never retry a create — Odoo has no idempotency key

A cold-start test was asked to create **one** customer. It created duplicates,
because some calls looked like they had failed — see
pattern 2 — and it tried again.

`create` always creates. There is no upsert, no unique key, no "create if
missing". The check has to happen before:

```python
w.create("res.partner", {"name": "<example customer>", "email": "<example email>"},
         unique_on=["name"])          # returns the existing id if found
```

Two identical calls should resolve to one record when the uniqueness check works.

```
first_id=<example id> second_id=<same id> -> no duplicate
total records with that name: 1
```

Duplicates in an ERP are not cosmetic: invoices, orders and payments attach
to the wrong record, and merging partners afterwards is manual work.

---

## 9. Report per step, from re-read state — never from intent

The same test ended with **"all requested tasks completed and verified"**. What the
database actually held:

| Claimed | Reality |
|---|---|
| customer created | ✓ but **four times** |
| internal note added | **never written** |
| order invoiced | **no invoice created during the run** |
 | "invoice <example reference> posted" | that invoice belonged to an **earlier, unrelated test** |

The agent described what it had attempted, then went looking for something
that matched the description. That is how a report becomes fiction.

`Writer.report()` takes what was ASKED and how to prove it, and refuses to
call anything done that a query cannot confirm:

```python
w.report([
    ("customer created", "res.partner", [["name", "=", NAME]], 1),
    ("internal note",    "res.partner",
     [["name", "=", NAME], ["comment", "!=", False]], 1),
])
```

```
  OK   customer created: found <example count>, expected <example count>
  NOT DONE internal note: found <example count>, expected <example count>

  NOT everything was done. Report only the OK lines as done.
```

It also flags counts above the expected number as probable duplicates.

**Rule: if a step cannot be proven with a query, it is not done.** Say so.
A partial result reported honestly is useful; a complete result reported
falsely destroys trust in every other number in the answer.

---

## 10. Guard the STEP, not just the record

`unique_on` stops duplicate partners. It does not stop this, which happened
on the second cold-start run:

```
several sale orders for one customer
orphan draft invoices never posted
one invoice actually completed (`<example reference>`)
```

The retry did not happen inside `create` — it happened one level up, around
the *whole step*. The agent created an order, could not tell whether it had
worked, and started the chain again from the top. Three times.

So make the step itself idempotent:

```python
ids, created = w.step(
    "order for this customer",
    [["partner_id", "=", pid]], "sale.order",
    lambda: w.create("sale.order", {...}))
```

Three identical calls should resolve to one order:

```
attempt 1: ids=[<example id>] created_now=True
attempt 2: ids=[<example id>] created_now=False
attempt 3: ids=[<example id>] created_now=False
total orders: 1
```

`step()` also refuses to stay silent when the work produced nothing:

```
step 'x' ran but produced nothing (0 -> 0). Do not retry blindly:
find out why before calling it again.
```

**A chain of writes needs a checkpoint per link.** Without one, a failure in
the middle is indistinguishable from a failure at the start, and the natural
reaction — start over — multiplies the damage instead of repairing it.

---

## 11. A method returning a dict did nothing — it asked for a form

`action_cancel` looks like it cancels. It does not:

```
action_cancel([sid])
  -> returns dict: {'res_model': 'sale.order.cancel', 'view_mode': 'form', ...}
  -> order state afterwards: 'sale'      # unchanged
```

What came back is not a result, it is an **instruction for the UI**: "open
this wizard form". In the web client the user then fills it in and confirms.
Over the API nothing happens until you do the same thing yourself:

```python
r = odoo.call("sale.order", "action_cancel", [[sid]], {"context": CTX})
if isinstance(r, dict) and r.get("res_model"):        # it wants a wizard
    wctx = dict(CTX, active_model="sale.order", active_ids=[sid],
                active_id=sid, **(r.get("context") or {}))
    wid = odoo.call(r["res_model"], "create", [{}], {"context": wctx})
    odoo.call(r["res_model"], "action_cancel", [[wid[0]]], {"context": wctx})
# state afterwards: 'cancel'
```

**Rule: if an action method returns a dict with `res_model`, the work has not
been done.** The return value is telling you which wizard to run. Treating it
as success is how a "cancelled" order stays confirmed.

This is pattern 1 again from another angle — and the reason `Writer.act()`
always re-reads the watched field instead of inspecting what came back.

---

## 12. Always name the fields — and never guess a state

The third write run created an invoice, then could not read it back, and
reported:

> *"Stato presumibile: draft (non postata tramite API)"*

The invoice was `posted`, number `<example reference>`. Everything had worked; the
agent understated its own result because a read had failed.

The read failed because it asked for **all** fields. On `account.move` that
is many columns including binary and computed ones. Naming three fields
always works:

```python
w.state_of("account.move", iid)
# {'name': '<example reference>', 'state': 'posted',
#  'payment_state': 'in_payment', 'amount_residual': '<example amount>'}
```

Two rules come out of this:

1. **Never call `read` without `fields`.** It is slow at best and fails at
   worst.
2. **Never report a state you did not read.** "Presumably draft" is a guess
   wearing the clothes of a fact. If the read fails, say the read failed —
   then fix the read.

Under-reporting is less damaging than over-reporting, but it is the same
defect: describing intent instead of the database.

---

## Before any write, in order

1. **Which company?** It must be able to do the operation (journal, etc.).
2. **Does it already exist?** Use `unique_on=` — a retried create is a
   duplicate, and Odoo will not stop you.
3. **Is the transition allowed?** `w.can(...)` — do not use exceptions as
   control flow.
4. **What is the undo?** If there is none (`action_post`), say so to the user
   before crossing the line.
5. **Act**, then **re-read**. `Writer` records before/after for every call.
6. **Report with `w.report(...)`**, one line per requested step, each proven
   by a query. Anything unproven is NOT DONE — say it plainly.

---

## NOTES — hand-written, preserved across regenerations

Add pitfalls here as you hit them.
