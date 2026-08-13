# Recipes — the queries people actually ask for

Each entry is a real business question, the trap it hides, and the query
that answers it. Verify each query on the target Odoo 18 instance.

Always pass the company context: `CTX = {"allowed_company_ids": [...]}`
from `query.py`.

## Installed is not the same as used

A module being installed says nothing about whether anyone uses it. Check the
data before describing a capability as active — and say which of the two you
are reporting.

Illustrative structure — measure these values on the target instance:

| Module | Installed | Actually used |
|---|---|---|
| `<module>` | yes | `<example usage finding>` |
| `<module>` | yes | `<example source count>`, but `<example record set>` has `source_id = False` |
| `mailing` | yes | `<example contact count>`, `<example campaign count>` campaign(s) |
| `purchase` | yes | `<example order count>` against `<example bill count>` vendor bills |

```python
# capability exists?
"custom_flag" in odoo.fields_get("account.move", [], ["type"])
# capability used?
odoo.search_count("account.move", [["custom_flag", "=", True]], CTX)
```

Saying "we have Facebook lead integration" when no lead carries a source is
technically true and practically misleading. Say: *the connector is installed,
but no lead currently records a source.*

---

## Connecting to a production instance (https)

Two things differ from a dev box, and together they cost one cold session
eight failed attempts:

**1. `list_db` is off.** `/xmlrpc/db list()` raises instead of listing, so the
database name cannot be discovered that way. The client now falls back to
`/web/session/get_session_info`, which reports it without credentials:

```
--url https://odoo.example.com --key <KEY>
  database   <discovered database>
  login      <API-key owner>  (resolved from the API key, not supplied)
```

**2. The interpreter may have no CA certificates.** A stock python.org build
on macOS trusts nothing until `Install Certificates.command` is run:

```
python3          3.8.10   CERTIFICATE_VERIFY_FAILED on every https call
python3.11                works
```

The failure is deceptive: discovery returns nothing, so the error reads
*"XML-RPC transport needs ODOO_DB"* — a database problem that is really a TLS
problem. The client now checks the trust store, says so explicitly, and falls
back to `certifi`.

If you see certificate errors, run with a working interpreter:

```bash
/opt/homebrew/bin/python3 scripts/query.py --url https://... --key ...
```

## "How many leads do we have?"

**Trap:** `crm.lead` holds leads AND opportunities. A count without `type`
answers neither question.

```
type=lead         total <example count>     active <example count>
type=opportunity  total <example count>    active <example count>
```

If leads are rare compared with opportunities, the "Leads" setting may be off
and everything may enter as an opportunity — say that, do not report a raw
lead count as a business fact.

**Converting one is not a status change: it CREATES customer records.** With
both `partner_name` and `contact_name` it creates **two** (company + contact,
linked); with neither, it creates a partner named after the lead's title.
Read the generated CRM reference before converting.

## "Has this invoice been paid?"

**Trap:** `payment_state = 'paid'` is not proof the money arrived, and
`payment_state` has seven values, not four — `reversed` (cancelled by a
credit note) and `blocked` are neither paid nor unpaid.

Three levels; measure the payment population on the target instance:

```
state = paid            what the record thinks
move_id set             <example count> — an accounting entry exists
is_matched = True       <example count> — the BANK confirmed it
```

Answering from `state` alone can overstate collected cash. Full detail
and the `payment_account_id` trap: `payments.md`.

## "Open", "active", "pending" are never a raw total

When someone asks how many tickets, tasks, orders or leads are *open*, they
mean the ones still requiring work — not every record ever created. The
difference is usually large:

| Asked | Raw total | Actually open |
|---|---:|---:|
| tickets | <example total> | **<example open>** |
| leads | <example total> | **<example active>** (some archived) |
| projects | <example total> | depends on stage |

Find the state field first, look at its values, then filter. Reporting the
raw total answers a question nobody asked and inflates the picture.

```python
odoo.fields_get("helpdesk.ticket", ["stage_id"], ["string", "type"])
odoo.search_read("helpdesk.stage", [], ["name", "sequence", "fold"])
```

---

## Money — read this before summing anything

**Never sum `amount_total`. Use `amount_total_signed`.**

`amount_total` is in the *invoice's own currency*. Summing it adds euros to
Czech crowns to dollars as if they were the same unit. `amount_total_signed`
is converted to the *company* currency and sign-correct (credit notes
subtract).

Illustrative multi-currency comparison — obtain counts from the target instance:

```
amount_total          <example total>     <- meaningless: currencies added up
amount_total_signed    <example signed total>     <- the real figure

Top customer, same data:
  amount_total          <example total>      <- overstated
  amount_total_signed    <example signed total>      <- correct
```

Even a small number of foreign-currency invoices can inflate the total sharply. A
single foreign-currency invoice is enough to make a report wrong, and
nothing in the output looks suspicious — the number is just large.

The same applies to `amount_untaxed` / `amount_untaxed_signed` and
`amount_residual` / `amount_residual_signed`. **If a field has a `_signed`
twin, the twin is the one you sum.**

---

## Money — recipes

### "Who are our biggest customers?"

**Trap:** `credit_limit` and `customer_rank` look relevant. They are not —
one is a credit ceiling, the other a sort weight. Neither is revenue.

Revenue means summing posted customer invoices, grouped by partner:

```python
rows = odoo.read_group("account.move",
    [["move_type", "=", "out_invoice"], ["state", "=", "posted"]],
    ["amount_total_signed:sum"], ["partner_id"], CTX)
top = sorted(rows, key=lambda r: -(r["amount_total_signed"] or 0))[:5]
```

Already precomputed: `python3 scripts/query.py top-customers`

### "How much have we invoiced?"

Only `posted` counts. Draft invoices are not revenue.

```python
odoo.read_group("account.move",
    [["move_type", "=", "out_invoice"], ["state", "=", "posted"]],
    ["amount_total_signed:sum"], [], CTX)
```

### "How much have we collected?"

**Trap:** `account.payment` holds both inbound and outbound payments. Summing
it all, or reading it without `payment_type`, tells you nothing about
collections — and finding "0 collected" next to millions invoiced should
prompt a check of the query, not a conclusion about the business.

```python
odoo.read_group("account.payment",
    [["payment_type", "=", "inbound"], ["state", "in", ["posted", "paid"]]],
    ["amount_company_currency_signed:sum"], [], CTX)
```

Outstanding is better read from the invoices themselves:

```python
odoo.read_group("account.move",
    [["move_type", "=", "out_invoice"], ["state", "=", "posted"],
     ["payment_state", "not in", ["paid", "reversed"]]],
    ["amount_residual_signed:sum"], [], CTX)
```

### "Any overdue unpaid invoices?"

**Trap:** `payment_state != 'paid'` alone is wrong — it catches reversed and
partially-paid invoices, and ignores whether the due date has passed.

```python
from datetime import date
odoo.search_count("account.move", [
    ["move_type", "=", "out_invoice"],
    ["state", "=", "posted"],
    ["payment_state", "not in", ["paid", "reversed"]],
    ["invoice_date_due", "<", date.today().isoformat()],
], CTX)
```

### "Unpaid vendor bills?"

Same shape, `move_type = in_invoice`.

---

## Sales

### "How many quotations are pending?"

In Odoo a quotation is `sale.order` before confirmation. Two states:
`draft` (never sent) and `sent` (waiting on the customer). "Pending a reply"
usually means both.

```python
odoo.search_count("sale.order", [["state", "in", ["draft", "sent"]]], CTX)
```

### "Confirmed but not invoiced?"

```python
odoo.search_count("sale.order",
    [["state", "=", "sale"], ["invoice_status", "=", "to invoice"]], CTX)
```

**Trap:** counting `state = sale` alone returns every confirmed order ever,
including fully invoiced ones.

### "Best selling products?"

```python
odoo.read_group("sale.order.line",
    [["state", "in", ["sale", "done"]]],
    ["product_uom_qty:sum", "price_subtotal:sum"], ["product_id"], CTX)
```

---

## Customers

### "How many customers do we have?"

Ambiguous — say which reading you used:

```python
odoo.search_count("res.partner", [["customer_rank", ">", 0]], CTX)      # ever sold to
odoo.search_count("res.partner", [["is_company", "=", True]], CTX)      # companies
```

A contact is a person inside a company; a company is the billable entity.
Counting both together double-counts.

### "Customers with no invoice in 6 months"

```python
from datetime import date, timedelta
cutoff = (date.today() - timedelta(days=182)).isoformat()
recent = {r["partner_id"][0] for r in odoo.read_group("account.move",
    [["move_type", "=", "out_invoice"], ["state", "=", "posted"],
     ["invoice_date", ">=", cutoff]], [], ["partner_id"], CTX)
    if r.get("partner_id")}
allc = {p["id"] for p in odoo.search_read("res.partner",
    [["customer_rank", ">", 0]], ["id"], context=CTX)}
dormant = allc - recent
```

### "Find customer X"

Names are entered inconsistently. Search wide, then show what you found:

```python
odoo.search_read("res.partner", [["name", "ilike", "rossi"]],
    ["name", "phone", "email", "is_company", "parent_id"], context=CTX)
```

If several match, list them rather than picking one.

---

## Projects

### "Projects in progress / late"

There is no built-in "late" flag. Late = has a deadline in the past and is
not closed:

```python
from datetime import date
odoo.search_read("project.task",
    [["date_deadline", "<", date.today().isoformat()],
     ["state", "not in", ["1_done", "1_canceled"]]],
    ["name", "project_id", "date_deadline", "user_ids"], context=CTX)
```

**Trap:** task state keys are prefixed (`01_in_progress`, `1_done`). Read
them with `fields_get` before filtering — do not guess.

### "Hours logged"

Timesheets live in `account.analytic.line`, not in the task:

```python
odoo.read_group("account.analytic.line",
    [["project_id", "!=", False]], ["unit_amount:sum"], ["project_id"], CTX)
```

Per person: group by `employee_id`. For "this month" add
`["date", ">=", "YYYY-MM-01"]`.

### "Tasks with nobody assigned"

`user_ids` is many2many — the empty test is `= False`:

```python
odoo.search_count("project.task", [["user_ids", "=", False]], CTX)
```

---

## Support

### "Open tickets"

**Trap:** there is no `is_close` field on `helpdesk.stage` in Odoo 18 — that
is Odoo 16 vintage. The field is `fold`: folded stages are the closed ones
(Solved, Cancelled).

```python
odoo.search_count("helpdesk.ticket", [["stage_id.fold", "=", False]], CTX)
```

The gap is large and easy to get wrong: an instance may have many tickets in
total but only a few open — the rest are Solved or Cancelled. Answering with
the total to "how many open tickets?" overstates the backlog.

Always list the stages before filtering:

```python
odoo.search_read("helpdesk.stage", [], ["name", "sequence", "fold"])
# 0 New F · 1 In Progress F · 2 On Hold F · 3 Solved T · 4 Cancelled T
```

### "Tickets older than a week"

```python
from datetime import date, timedelta
cutoff = (date.today() - timedelta(days=7)).isoformat()
odoo.search_count("helpdesk.ticket",
    [["create_date", "<", cutoff], ["stage_id.fold", "=", False]], CTX)
```

---

## CRM

### "Open opportunities"

**Trap:** `crm.lead` hides archived records by default. An instance may contain
archived records that a plain query omits.
Say which you mean:

```python
odoo.search_count("crm.lead", [["type", "=", "opportunity"]], CTX)  # active only
CTX_ALL = dict(CTX, active_test=False)
odoo.search_count("crm.lead", [["type", "=", "opportunity"]], CTX_ALL)  # incl. archived
```

### "Pipeline value by stage"

```python
odoo.read_group("crm.lead", [["type", "=", "opportunity"]],
    ["expected_revenue:sum"], ["stage_id"], CTX)
```

---

## Subscriptions

State keys are numeric-prefixed and NOT guessable — read them first:

```python
odoo.fields_get("sale.order", ["subscription_state"], ["selection"])
# 1_draft 2_renewal 3_progress 4_paused 5_renewed 6_churn 7_upsell
```

Running subscriptions:

```python
odoo.search_count("sale.order",
    [["subscription_state", "in", ["3_progress", "4_paused"]]], CTX)
```

---

## When a question does not map

Some questions assume structure Odoo does not have. Say so plainly instead
of producing a number that means something else:

| Asked | Reality |
|---|---|
| "invoices by department" | Odoo has no departments on invoices — analytic accounts or sales teams are the nearest equivalent |
| "stock levels" | only if the Inventory module is installed — check the profile |
| "who is on holiday" | only with the Time Off module |

Check the profile before declaring something absent.
