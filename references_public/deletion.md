# Deleting a company and its data

Merged from four older skills (`odoo-18-company-deletion`,
`odoo-company-cleanup`, `odoo-data-cleanup`, and the deletion sections of the
accounting skills). **This is the most dangerous operation in the skill.**

> Two things are separated below on purpose:
> **VERIFIED** — verify on the target Odoo 18 instance, with the command.
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

Measure the target instance first, to understand the scale of its company setup:

```
                       company to keep    company to delete
account.move             <example count>   <example count>
account.move.line        <example count>   <example count>
account.payment          <example count>   <example count>
account.journal          <example count>   <example count>
account.account          <example count>   <example count>
```

## The order — leaf to root (verify that every required model exists)

Each model must be empty before the one below it can go. Counts are the
live totals, so you can see what you are up against:

| # | Model | Records | Note |
|---|---|---|---|
| 1 | `account.partial.reconcile` | `<example count>` | unlink directly |
| 2 | `account.payment` | `<example count>` | `action_cancel` **then** unlink |
| 3 | `account.move.line` | `<example count>` | usually goes with the move |
| 4 | `account.move` | `<example count>` | `button_draft` **then** unlink |
| 5 | `account.bank.statement.line` | `<example count>` | |
| 6 | `account.bank.statement` | `<example count>` | |
| 7 | `account.reconcile.model` | `<example count>` | |
| 8 | `account.payment.method.line` | `<example count>` | |
| 9 | `account.journal` | `<example count>` | |
| 10 | `account.tax.repartition.line` | `<example count>` | |
| 11 | `account.tax` | `<example count>` | |
| 12 | `account.tax.group` | `<example count>` | |
| 13 | `account.account` | `<example count>` | note: `company_ids`, not `company_id` |
| 14 | `account.payment.term` | `<example count>` | |
| 15 | `res.partner.bank` | `<example count>` | |
| 16 | `ir.default` | `<example count>` | defaults pointing at deleted records |
| 17 | `ir.rule` | `<example count>` | record rules referencing the company |
| 18 | `resource.calendar` | `<example count>` | |
| 19 | `res.company` | `<example count>` | last |

## The context trap (verify before deletion)

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
  recreates; older reports describe needing **several rounds** of the same
  unlink before a model reaches zero. Loop until `search_count` returns 0,
  do not assume one pass is enough.
- **Small batches.** Larger unlinks may time out or fail on
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
        model, [["company_id", "=", cid]], ["id"], limit=batch_size, context=CTX)]
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
