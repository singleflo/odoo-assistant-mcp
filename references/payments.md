# Payments, reconciliation and version differences

Merged from nine older Odoo skills. **Every claim below was re-verified on a
live Odoo 18.0 Enterprise instance** before being kept — two of them turned
out to be wrong as originally written, and are corrected here.

```python
import sys; sys.path.insert(0, "scripts")
from odoo_client import connect
odoo = connect()
CTX = {"allowed_company_ids": [1, 2]}
```

---

## Field names that changed in Odoo 18

Querying the old name returns `ValueError: Invalid field 'X' on model 'Y'`.
All six verified against `fields_get` on a live 18.0 instance:

| Model | Odoo ≤17 | Odoo 18 |
|---|---|---|
| `account.account` | `user_type_id` | **`account_type`** — now a selection string, not a m2o |
| `account.account` | `company_id` | **`company_ids`** — many2many |
| `account.account` | `reconciled` | **`reconcile`** — boolean, "allows reconciliation" |
| `account.journal` | `payment_debit_account_id`, `payment_credit_account_id` | **removed** — moved to `account.payment.method.line` |
| `account.bank.statement.line` | `to_process` | **removed** — use `state` + `is_reconciled` |

**Rule: run `fields_get` before composing a query on an accounting model.**
Documentation and older skills lag behind the schema.

## `payment_state` has seven values, not four

Older notes list four. The instance reports:

```python
odoo.fields_get("account.move", ["payment_state"], ["selection"])
# not_paid · in_payment · paid · partial · reversed · blocked · invoicing_legacy
```

| Value | Meaning |
|---|---|
| `not_paid` | nothing registered |
| `in_payment` | payment registered, **not yet reconciled with the bank** |
| `partial` | partially paid |
| `paid` | settled |
| `reversed` | cancelled by a credit note |
| `blocked` | manually blocked |
| `invoicing_legacy` | imported from a legacy system |

Treating anything other than `paid` as "unpaid" is wrong: `reversed` and
`blocked` mean something else entirely.

**`in_payment` is not `paid`.** The UI hides the "Amount Due" field in that
state, so a stuck payment looks settled to the user while the residual is
still outstanding.

## `account.payment` state machine

```
draft → in_process → paid
  ↓          ↓
canceled  rejected
```

All five states verified present. Methods: `action_validate()`,
`action_post()`, `action_draft()` (to re-validate after a config fix),
`action_cancel()`, `action_reject()`.

## Registered, posted, reconciled — three different things

This is where money questions go wrong. Measured on 107 payments:

```
move_id + is_matched : 11    posted AND reconciled with the bank
move_id, not matched : 72    posted, bank has not confirmed
no move_id           : 24    NO accounting entry exists at all
```

- **`state`** says what the payment record thinks it is.
- **`move_id`** says whether an accounting entry exists.
- **`is_matched`** ("Is Matched With a Bank Statement") says whether the bank
  confirmed it.

Only `is_matched = True` means the money actually arrived. Reporting a
payment as received on `state = paid` alone overstates cash by **7×** on this
instance (11 truly matched vs 83 with an entry).

## CORRECTION: it is not "electronic payments have no move_id"

The older skill stated that electronic payments never generate a journal
entry on validation. The data says something more precise:

```
Stripe (Stripe)        move_id=NO   11
Stripe (Persevida SL)  move_id=NO    4
Stripe (False)         move_id=SI    2      <- electronic WITH an entry
Manuale (BBVA)         move_id=NO    6      <- manual WITHOUT one
Manuale (WISE EUR)     move_id=SI   55
```

**Missing entries are not a property of the payment method.** Manual BBVA
payments lack `move_id` too, and two Stripe payments have one. The pattern
that does hold: everything without `move_id` sits in `draft` or `in_process`
— the entry appears when the payment is posted, and for electronic providers
that usually happens at bank reconciliation, not at validation.

So: **do not infer from the method. Read `move_id`.**

## The `payment_account_id` trap

`account.payment.method.line.payment_account_id` is the outstanding/transit
account. If it is `False`, Odoo cannot build the journal entry and the
payment silently stalls.

Symptoms, in order of appearance:
1. payment stays `in_process` after the provider confirms
2. payment reaches `paid` but `move_id` stays `False`
3. invoice flips to `in_payment` while the residual does not move
4. the user sees no "Amount Due"
5. **the follow-up cron chases the customer for an invoice that was paid**

```python
# audit every line
odoo.search_read("account.payment.method.line", [],
    ["name", "journal_id", "payment_account_id", "default_account_id"], CTX)
```

A correct line has **both** `payment_account_id` (transit) and
`default_account_id` (final liquidity). On the reference instance all 8 lines
were configured — the trap is real but not present here.

Fix, when it is:

```python
w.write("account.payment.method.line", line_id,
        {"payment_account_id": account_id})     # asset_current, reconcile=True
```

Find a suitable account:

```python
odoo.search_read("account.account",
    [["account_type", "=", "asset_current"], ["reconcile", "=", True],
     ["company_ids", "in", [company_id]]], ["code", "name"], CTX)
```

## Linking a payment to an invoice

```python
w.write("account.payment", pid, {"invoice_ids": [[6, False, [invoice_id]]]})
```

`[6, False, [ids]]` is Odoo's "replace the whole list" m2m command. This
populates `reconciled_invoice_ids` and moves the invoice out of `not_paid`.

## `payment.transaction` — the provider bridge

60 records on the reference instance:

```
done 19 · pending 25 · error 4 · draft 12
```

A transaction in `done` does not imply the payment is reconciled: `S00411-1`
was `done` while `S00411` was `error` on the same order. Read the payment,
not the transaction, to answer "was this invoice paid".

---

## Multi-company: `AccessError` you cannot diagnose

On a multi-company instance, a call with the wrong company context fails:

```
odoo.exceptions.AccessError: Access to unauthorized or invalid companies
```

Verified: passing `allowed_company_ids: [99]` raises it immediately.

The nasty part: **`res.company` itself throws the same error**, so you cannot
query your way to the right id. If you do not know it, try small integers
(1, 2, 3…) until one succeeds — or read it from the profile, which
`census.py` stores:

```python
odoo.search_read("res.company", [], ["name"], context=CTX)
# [(2, 'Persevida S.L.'), (1, 'FL1 s.r.o.')]
```

## Italian e-invoicing (FatturaPA / SDI)

**Status on the reference instance: not demonstrable.** `l10n_it_edi_state`
is set on **0** invoices, so the failure below could not be reproduced here.
The 14 `l10n_it_*` fields exist, so the module is installed but unused.

The reported failure, kept because the mechanism is documented in the field
mapping:

> Odoo fills `<DatiFattureCollegate><IdDocumento>` from `account.move.ref`
> whenever it is non-empty, assuming it holds a related invoice number. The
> SDI schema requires Basic Latin only, max 20 characters — so an accented or
> long free-text note in `ref` fails XSD validation.

Measured exposure: **313 of 1061 invoices with a `ref` exceed 20 characters**
(e.g. `Differimento del FT/2026/0020`), though **none** contain non-ASCII
characters on this instance. If Italian EDI is switched on, those 313 would
need review.

Diagnosis, when it happens:

```python
odoo.search_read("account.move", [["id", "=", mid]],
    ["ref", "payment_reference", "l10n_it_edi_state",
     "l10n_it_edi_attachment_id"], CTX)
```

`l10n_it_edi_state = processing` with an empty attachment means the XML never
finished generating. Fix by shortening `ref` to plain ASCII ≤20 chars and
moving the note elsewhere (`narration`).

---

## What did NOT survive the merge

For the record, so nobody re-imports it:

- **The old API scripts** (`call_method.py`, `search_read.py`, …) — `/json/2`
  only, `sys.exit(1)` instead of exceptions, token hardcoded as a default.
  `odoo_client.py` supersedes them with auto-detected transport.
- **Instance profiles** (`persevida-profile-2026-08.md`) — instance-specific
  data does not belong in a skill. `census.py` regenerates it on demand.
- **Oracle review logs** — process history, not instructions.
- **Company-deletion playbooks** — see `deletion.md`; the ordering is real
  but the commands were rewritten for the current client.

---

## NOTES — hand-written, preserved across regenerations
