# Documents and notifications

Two things Odoo does very differently from what the API surface suggests.
Everything below was measured on a live instance.

```python
import sys; sys.path.insert(0, "scripts")
from odoo_client import connect
from documents import Documents

d = Documents(connect(), company_id=2)
```

---

# Part 1 — Documents

## Every binary lives in one of two places

| Shape | Where | Read it with |
|---|---|---|
| **attachment** | `ir.attachment` row, `res_model` + `res_id` point at the record | `d.attachments(model, id)` → `d.read_binary(att_id)` |
| **binary field** | a column on the record itself | `d.field_binary(model, id, field)` |

Both are base64 in a field. There is no third mechanism: chatter uploads,
imported files, product images, signatures, generated reports — all one of
these two. `d.download(model, id)` covers both in one call.

**The chatter is not a separate store.** A file dropped in the chatter is an
`ir.attachment` pointing at the record. Same query as everything else.

```python
d.attachments("project.task", 1363)
# 9 rows: flusso_commessa.pdf, 076_MSC_Magnifica_GYM.xlsx, ...
d.binary_fields("account.move")
# ['invoice_pdf_report_file', 'l10n_es_edi_facturae_xml_file', ...]
```

Large files are fine over XML-RPC: an 8.4 MB attachment came back as 11.2 M
characters of base64 and decoded intact. Check `file_size` first if you care
about the round trip.

## The trap: `datas` comes back empty on a valid attachment

Odoo keeps attachment bytes on **disk** (the filestore) and only the path in
`store_fname`. Restore a database without its filestore directory and you get
thousands of attachments with perfect metadata and no content — and Odoo
returns empty instead of raising.

Measured on the reference dev instance:

```
d.missing_content(sample=30)
# {'sampled': 30, 'readable': 12, 'missing': 18}
```

**18 of 30 attachments had no file.** So when a download returns nothing, ask
which of the two it is before debugging code that works:

```python
d.download("project.task", 1363)
# {'saved': [], 'skipped': [
#   ('flusso_commessa_odoo.drawio.pdf',
#    'the record says 89202 bytes at filestore path 6f/6f85016501...'),
#   ...]}
```

An earlier version returned an empty list silently — indistinguishable from
"this record has no attachments". Never let a missing file look like an
absent one.

## Generating a PDF is not one call

Three routes look right and are not:

```
_render_qweb_pdf(...)              -> private, Odoo refuses it outright
report_action(...)                 -> returns a UI action, renders nothing
GET /report/pdf/<report>/<id>      -> needs a SESSION COOKIE
```

The HTTP route is the one people reach for, and it is closed to API keys:
`/web/session/authenticate` answers `AccessDenied` for a key — it wants the
account password. With key-only access, forget the URL.

What works: **the model's own print/send wizard renders the PDF and stores
it**. For invoices it lands in `invoice_pdf_report_file` — a binary FIELD,
not an attachment. Looking only at `ir.attachment` finds nothing and makes
you conclude it failed, while the PDF is right there.

```python
d.generate_pdf("account.move", 5775)
# /tmp/account_move_5775.pdf   20608 bytes, magic b'%PDF-'
```

For a model with no known wizard, `generate_pdf` raises and tells you what it
found instead of guessing:

```
No known print wizard for project.task. Reports available:
['hr_timesheet.report_project_task_timesheet']. Render them from the UI,
or check the model's binary fields: [...]
```

`d.reports_for(model)` lists what the user sees under **Print** — 36 reports
on this instance, including in-house ones (`preinvoice_base.report_...`).

---

# Part 2 — Notifications

> **The rule that matters: the chatter is not private.** On the reference
> instance, **12 of 12** sampled sales orders had a customer among their
> followers. A `mt_comment` on any of them emails that customer. Before
> writing anything on a record, run `d.audience(model, id)`.

## Choose the channel before writing a word

| Goal | Call | Reaches | Emails customers |
|---|---|---|---|
| tell a colleague | **`d.tell(...)`** | exactly the users you name | **no** |
| leave an internal trace | `d.notify(..., "mail.mt_note")` | internal followers | no |
| talk to the customer | `d.notify(..., "mail.mt_comment")` | **all followers** | **YES** |
| make someone act by a date | `d.schedule(...)` | one user's To-Do | no |

Measured on the same order, same recipient list:

```
mt_note      -> 1 notification  (Daniel, inbox)      0 emails
mt_comment   -> 2 notifications (Daniel, inbox)
                                (3v di veronesi, EMAIL)   1 email
```

The second recipient was never requested. It came from the follower list.

## `tell()` — the safe default

`message_notify` under the hood. It reaches the named partners and touches
nothing else:

```python
d.tell("sale.order", sid, "<p>Please check the payment terms.</p>",
       users=[7], subject="Check needed")
# notified : [{'partner': 'Daniel De Vecchi', 'via': 'inbox', 'status': 'sent'}]
# emails   : []
# followers: 3 -> 3      (unchanged — the customer heard nothing)
```

Use this whenever the message is for staff. `message_post` subscribes people
and fans out to followers; `message_notify` does neither.

## Check the audience first

```python
d.audience("sale.order", sid)
# followers: ['Roberto Crotti', '3v di veronesi e vai snc', 'Daniel De Vecchi']
# internal : ['Roberto Crotti', 'Daniel De Vecchi']
# external : ['3v di veronesi e vai snc']    <- a real customer
```

"External" means: a follower with no internal user behind it. Those partners
receive every `mt_comment` by email.

## Can this instance even send email?

```python
d.mail_works()
# servers    : ['neutralization - disable emails (invalid:1025)']
# neutralised: True
# queue      : {'outgoing': 2, 'sent': 95, 'exception': 8}
# verdict    : "Email is NEUTRALISED on this instance — nothing leaves.
#               Do not tell anyone they were emailed."
```

Odoo neutralises mail on restored databases: host `invalid`, port 1025.
Messages are marked *sent* into a void. **Check before promising delivery** —
"the customer has been notified" is false on a neutralised instance.

Real failures show up too:

```
state=exception  error='Il server "all fl1" non può essere usato perché è archiviato.'
```

An archived mail server silently breaks every outgoing email. Two messages
from July are still `outgoing` on this instance — nobody noticed.

## Users and partners are different tables

`message_post` and `message_notify` take **partner** ids, not user ids.
Passing a user id notifies whoever happens to own that partner id, or nobody.
`tell()` and `notify()` resolve `users=[...]` for you.

## Odoo never notifies the author

Ask for two recipients while being one of them and exactly one notification
is created:

```
requested    : [3, 9]
notified     : [{'partner': 'Daniel De Vecchi', 'via': 'inbox'}]
not_notified : [3]
note         : "Odoo never notifies you of your own message"
```

Reporting "notified 2 users" there would be false. Every call returns a
delivery report read back from `mail.notification` and `mail.mail`.

## `message_post` raises even when it works

It returns a `mail.message` recordset, which XML-RPC cannot serialise:

```
KeyError: <class 'odoo.api.mail.message'>
```

The message **is** posted. Write pattern 2 in another costume —
`OdooExecutedButUnserializable`. Retrying produces a duplicate message.

## `notification_type` decides inbox vs email

Every user has a preference:

```
Roberto Crotti      admin           inbox     bo@fl1.cz
Daniel De Vecchi    bo2@fl1.cz      inbox     bo2@fl1.cz
```

With `inbox`, notifications stay inside Odoo — **no email is generated at
all**. Switching the same user to `email` turned one inbox notification into
a queued `mail.mail`. You cannot force an email by choosing a subtype: the
recipient's preference wins.

## Activities — the only notification with a deadline

A message is passive. An activity appears in the user's **To Do** with a due
date and chases them.

```python
d.schedule("sale.order", sid, "Call the customer back",
           user_id=2, days=2, note="<p>Check availability.</p>")
# {'summary': 'Call the customer back', 'user_id': [2, 'Roberto Crotti'],
#  'date_deadline': '2026-08-13'}
```

Activity types are per-instance — this one has 17, several bound to a model:

```
Email +0d · Call +2d · Meeting +0d · To-Do +5d · Upload Document +5d
Order Upsell (sale.order) · Tax Report Ready (account.move) ...
```

Read `mail.activity.type` instead of assuming; pass `activity_type` when the
type carries meaning.

## Querying `mail.message` can explode

```python
odoo.search_count("mail.message", [])
# Record does not exist or has been deleted. (Record: calendar.event(1177,))
```

Its record rules re-read the linked record for every row. One orphan message
pointing at a deleted record kills the whole query. **Always filter by
`model` and `res_id`:**

```python
odoo.search_count("mail.message",
                  [["model", "=", "sale.order"], ["res_id", "=", sid]])
```

## Before notifying anyone, in order

1. **`d.mail_works()`** — can email leave at all? If not, say so.
2. **`d.audience(model, id)`** — is a customer among the followers?
3. **Pick the channel**: `tell()` for staff, `mt_comment` only when you
   genuinely mean to write to the customer, `schedule()` when someone must act.
4. **Read the delivery report** — `notified`, `emails_generated`,
   `not_notified`. Never claim someone was reached without it.

---

## NOTES — hand-written, preserved across regenerations

Add pitfalls here as you hit them.
