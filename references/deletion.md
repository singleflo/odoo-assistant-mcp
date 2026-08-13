# Deleting a company and its data

Merged from four older skills (`odoo-18-company-deletion`,
`odoo-company-cleanup`, `odoo-data-cleanup`, and the deletion sections of the
accounting skills). **This is the most dangerous operation in the skill.**

> Two things are separated below on purpose:
> **VERIFIED** — re-checked on a live Odoo 18 instance, with the command.
> **REPORTED** — from the older skills, not re-verifiable without actually
> deleting. Treat as a hypothesis, confirm as you go.

---

## Before anything

1. **Take a backup.** Not a suggestion. There is no undo.
2. **Never on production.** `odoo_client.py` refuses production writes unless
   `ODOO_ALLOW_PROD_WRITE=yes` — do not set it to delete a company.
3. **Know the volume first.** Deleting a company means deleting everything
   below; measure before you start.

```python
for model in ("account.move", "account.move.line", "account.payment",
              "account.journal", "account.account"):
    print(model, odoo.search_count(model, [["company_id", "=", cid]], CTX))
```

Measured on the reference instance, to show the scale of a two-company setup:

```
                       FL1 s.r.o.    Persevida S.L.
account.move             2778             705
account.move.line        6416            1619
account.payment            94              13
account.journal             0               9
account.account           294             747
```

## The order — leaf to root (VERIFIED: all 19 models exist)

Each model must be empty before the one below it can go. Counts are the
live totals, so you can see what you are up against:

| # | Model | Records | Note |
|---|---|---|---|
| 1 | `account.partial.reconcile` | 1265 | unlink directly |
| 2 | `account.payment` | 107 | `action_cancel` **then** unlink |
| 3 | `account.move.line` | 8035 | usually goes with the move |
| 4 | `account.move` | 3483 | `button_draft` **then** unlink |
| 5 | `account.bank.statement.line` | 1973 | |
| 6 | `account.bank.statement` | 0 | |
| 7 | `account.reconcile.model` | 7 | |
| 8 | `account.payment.method.line` | 84 | |
| 9 | `account.journal` | 9 | |
| 10 | `account.tax.repartition.line` | 606 | |
| 11 | `account.tax` | 143 | |
| 12 | `account.tax.group` | 32 | |
| 13 | `account.account` | 1041 | note: `company_ids`, not `company_id` |
| 14 | `account.payment.term` | 19 | |
| 15 | `res.partner.bank` | 73 | |
| 16 | `ir.default` | 25 | defaults pointing at deleted records |
| 17 | `ir.rule` | 389 | record rules referencing the company |
| 18 | `resource.calendar` | 2 | |
| 19 | `res.company` | 2 | last |

## The context trap (VERIFIED)

Every call must run with the **remaining** company in context, not the one
being deleted:

```python
CTX = {"allowed_company_ids": [company_to_keep]}
```

With the wrong context you get `AccessError: Access to unauthorized or
invalid companies` — and `res.company` throws the same error, so you cannot
query your way out of it. See `payments.md`.

## REPORTED — not re-verified here

These come from the older skills. They describe behaviour during an actual
deletion, which cannot be reproduced without deleting:

- **Records regenerate.** Deleting `account.move` leaves lines that Odoo
  recreates; the older skills report needing **10–20 rounds** of the same
  unlink before a model reaches zero. Loop until `search_count` returns 0,
  do not assume one pass is enough.
- **Batches of 10–20.** Larger unlinks reportedly time out or fail on
  interdependencies. Small batches, repeated.
- **Posted moves must be drafted first.** `button_draft` then `unlink`.
  Payments need `action_cancel` first.
- **Partial cleanup is possible** — deleting invoices while keeping journals
  and the chart of accounts — by stopping after step 5.

## Doing it safely

Route every step through the safety layer, which classifies `unlink` as
**L4_DESTRUCTIVE** and refuses it without explicit confirmation:

```python
from write_patterns import Writer
w = Writer(odoo, company_id=company_to_keep)

while True:
    ids = [r["id"] for r in odoo.search_read(
        model, [["company_id", "=", cid]], ["id"], limit=20, context=CTX)]
    if not ids:
        break
    w.act(model, "unlink", ids, watch=None, confirmed=True)
    # re-count, do not trust the return value — see writing.md pattern 1
```

`Collab` refuses destructive calls outright; deletion has to be a deliberate
`Writer` call.

## Verification

Deletion is done when the count is zero, not when the call returned:

```python
{m: odoo.search_count(m, [["company_id", "=", cid]], CTX)
 for m in MODELS}
```

Anything non-zero means another round.

---

## NOTES — hand-written, preserved across regenerations
