# CRM — leads, opportunities and the conversion that creates customers

`crm.lead` holds **two different things** in one table, separated by the
`type` field. Everything below was measured on a live Odoo 18 instance,
including the conversion, which was run end to end and then cleaned up.

---

## 1. `type` is not cosmetic — it is two stages of a funnel

```python
odoo.fields_get("crm.lead", ["type"], ["selection"])
# [['lead', 'Lead'], ['opportunity', 'Opportunity']]
```

| | `type='lead'` | `type='opportunity'` |
|---|---|---|
| What it is | raw, unqualified interest | qualified, being worked |
| `partner_id` | usually **empty** | usually **set** |
| Contact data | loose fields on the lead itself | on the linked `res.partner` |
| Appears in | CRM → Leads | CRM → Pipeline (kanban) |
| Menu | hidden unless "Leads" is enabled | always |

**The menus are not interchangeable.** From the generated reference:

```
CRM/Leads     domain ['|', ('type','=','lead'), ('type','=',False)]
CRM/Pipeline  domain [('type','=','opportunity')]
```

A query on `crm.lead` with no `type` filter answers neither question.

## 2. Check whether leads are even enabled

Odoo hides the lead stage unless the "Leads" setting is on. When it is off,
**everything arrives directly as an opportunity** and `type='lead'` is
practically empty.

```python
odoo.search_count("crm.lead", [["type", "=", "lead"]], CTX)         # 2
odoo.search_count("crm.lead", [["type", "=", "opportunity"]], CTX)  # 89
```

Two leads against 89 opportunities means the funnel starts at the
opportunity stage on this instance. Reporting "we have 2 leads" as a
business fact would be misleading — the right answer is "leads are not used
here".

Confirm with the group:

```python
odoo.search_read("res.groups", [["name", "ilike", "lead"]], ["full_name"])
# Technical / Show Lead Menu — 9 users
```

## 3. Counting: three different numbers

```
type=lead         total 2     active 0
type=opportunity  total 89    active 12
```

Archived records are hidden by default (`active_test`). Always say which
number you are giving:

```python
# what the user sees in the pipeline
odoo.search_count("crm.lead", [["type", "=", "opportunity"]], CTX)
# everything ever, including archived
odoo.search_count("crm.lead", [["type", "=", "opportunity"]],
                  dict(CTX, active_test=False))
```

---

# The contact fields — where the data actually lives

A lead carries its contact information **as loose text**, not as a relation.
This is the whole point of a lead: nothing has been created in the address
book yet.

| Field | Type | Holds | Becomes, on conversion |
|---|---|---|---|
| `partner_id` | m2o → `res.partner` | the customer, once linked | the created/chosen partner |
| `partner_name` | **char** | company name as typed | a partner with `is_company=True` |
| `contact_name` | **char** | person's name as typed | a partner with `is_company=False` |
| `name` | char, **required** | the opportunity title | fallback partner name (see below) |
| `email_from` | char | email | `partner.email` |
| `phone` | char | landline | `partner.phone` |
| `mobile` | char | mobile | `partner.mobile` |
| `function` | char | job title | `partner.function` |
| `street` `city` `zip` `country_id` | char / m2o | address | copied to the partner |
| `title` | m2o → `res.partner.title` | Mr/Ms/Dr | copied |
| `website` `lang_id` | char / m2o | | copied |

Two fields are **computed and read-only** — never write them:

```
email_state   selection   Email Quality     readonly
phone_state   selection   Phone Quality     readonly
```

They report whether Odoo considers the address/number well-formed. On this
instance: 12 emails `correct`, 3 phones `correct`, 1 `incorrect`.

**`partner_name` is a char, not a relation.** Searching companies with
`[["partner_name", "=", "ACME"]]` finds leads whose typed company name is
ACME — it does *not* join `res.partner`. Once converted, the same
information lives in `partner_id.parent_id.name` instead.

Measured on the instance:

```
with partner_id     81      without partner_id   3
with contact_name   52      with partner_name   57
with email_from     71      with phone 13, mobile 34
```

---

# The conversion wizard — it CREATES CUSTOMERS

This is the part with lasting consequences. Converting is not a status
change: **it writes to the address book.**

## What the wizard is

```
crm.lead2opportunity.partner            single lead
crm.lead2opportunity.partner.mass       several at once
```

Key fields:

| Field | Values | Meaning |
|---|---|---|
| `name` | `convert` · `merge` | convert this one, or merge into an existing opportunity |
| `action` | `create` · `exist` · `nothing` | **what to do about the customer** |
| `partner_id` | m2o | required when `action='exist'` |
| `user_id` `team_id` | m2o | salesperson and team to assign |

The mass version adds `each_exist_or_create` and `deduplicate`.

## Running it

```python
ctx = dict(CTX, active_model="crm.lead", active_ids=[lid], active_id=lid)
wid = odoo.call("crm.lead2opportunity.partner", "create",
                [{"lead_id": lid, "name": "convert", "action": "create"}],
                {"context": ctx})
odoo.call("crm.lead2opportunity.partner", "action_apply",
          [[wid[0]]], {"context": ctx})
```

The context is **not optional** — the wizard reads the lead from
`active_ids`. And `action_apply` returns an action dict, so treat it as
pattern 11 in `writing.md`: re-read the lead to confirm.

## What `action='create'` actually creates — measured

A lead with **both** `partner_name` and `contact_name`:

```
before: res.partner = 593
after : res.partner = 595        <- TWO records, not one
```

```
partner_id -> [1451, 'ACME Test SRL, Mario Rossi']

  the contact                    the parent
  name       Mario Rossi         name       ACME Test SRL
  is_company False               is_company True
  parent_id  [1450, ...]         email      mario.rossi@…
  email      mario.rossi@…       phone      +39 02 1234567
  phone      +39 02 1234567
  mobile     +39 333 1234567
  function   Responsabile IT
  street/city/zip copied
  customer_rank 0
```

**The company's email and phone are copied from the lead too** — so the
organisation ends up carrying one person's contact details. Worth knowing
before converting a lead whose email is a personal address.

Note `customer_rank = 0`: the conversion does **not** mark them a customer.
That happens when the first sales order is confirmed. Counting customers by
`customer_rank > 0` will not include freshly converted opportunities.

## The full matrix — every case tested

| Lead has | Partners created | Result |
|---|---:|---|
| `partner_name` + `contact_name` | **2** | company + contact, linked by `parent_id` |
| `contact_name` only | 1 | person, `is_company=False`, no parent |
| `partner_name` only | 1 | company, `is_company=True` |
| **neither** | 1 | **partner named after the lead's `name`** |
| `action='nothing'` | 0 | opportunity with `partner_id=False` |
| `action='exist'` | 0 | links the partner you pass |

The fourth row is the dangerous one. A lead titled *"Richiesta informazioni
dal sito"* with no contact fields produces a customer record called
**"Richiesta informazioni dal sito"**. Verified:

```
partner_id -> [1454, "ZZEDGE ne l'uno ne l'altro"]
```

`name` is `required=True` on `crm.lead` and its label is "Opportunity", so
there is always something to fall back on — and it is always the wrong thing
to have in the address book.

## Before converting — the checklist

1. **Is it already linked?** If `partner_id` is set, use `action='exist'`;
   `create` would duplicate the customer.
2. **Does the customer already exist?** Search by email and by name before
   creating. Odoo has no upsert here — see `writing.md` pattern 8.
   ```python
   odoo.search_read("res.partner",
       ["|", ["email", "=", lead["email_from"]],
             ["name", "=ilike", lead["contact_name"]]], ["name"], CTX)
   ```
3. **Are `contact_name` / `partner_name` filled?** If both are empty, fill
   them first or use `action='nothing'` — do not let the lead title become a
   customer.
4. **Company or person?** `partner_name` → company, `contact_name` → person.
   Filling only `contact_name` for a business contact loses the company.
5. **Which company (multi-company)?** Set `company_id` on the lead; the
   partner inherits nothing from `allowed_company_ids` — see `writing.md`
   pattern 5.

## Undoing it

`type` is writable (`readonly=False`, stored), so an opportunity can be
forced back to `lead`:

```python
w.write("crm.lead", lid, {"type": "lead"})
```

**But the partner records stay.** There is no "unconvert" that cleans up the
address book — the two partners created above survive, and merging or
deleting them afterwards is manual work. Treat conversion as one-way.

## Won / lost

`won_status` (`won` · `lost` · `pending`) is separate from the stage.
Setting an opportunity lost:

```python
odoo.call("crm.lead", "action_set_lost", [[lid]], {"context": CTX})
# raises OdooExecutedButUnserializable — it WORKED, do not retry
```

Verified effect: `won_status='lost'`, `probability=0.0`, `active=False`
(archived). The stage does **not** change — a lost opportunity keeps the
stage it was in, which is why "how many in Proposition" must exclude
archived records.

---

## NOTES — hand-written, preserved across regenerations
