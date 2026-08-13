# Documents and notifications

Two things Odoo does very differently from what the API surface suggests.
Everything below is a methodology for checking a live Odoo instance.

```python
import sys; sys.path.insert(0, "scripts")
from odoo_client import connect
from documents import Documents

d = Documents(connect(), company_id=target_company_id)
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
d.attachments("project.task", record_id)
# rows: <example file>.pdf, <example spreadsheet>.xlsx, ...
d.binary_fields("account.move")
# ['invoice_pdf_report_file', 'l10n_es_edi_facturae_xml_file', ...]
```

Large files are fine over XML-RPC, but the base64 payload can be much larger
than the decoded file. Check `file_size` first if you care
about the round trip.

## The trap: `datas` comes back empty on a valid attachment

Odoo keeps attachment bytes on **disk** (the filestore) and only the path in
`store_fname`. Restore a database without its filestore directory and you get
thousands of attachments with perfect metadata and no content — and Odoo
returns empty instead of raising.

Measured on the reference dev instance:

```
d.missing_content(sample=sample_size)
# {'sampled': '<example count>', 'readable': '<example count>',
#  'missing': '<example count>'}
```

**Some sampled attachments may have no file.** So when a download returns nothing, ask
which of the two it is before debugging code that works:

```python
d.download("project.task", record_id)
# {'saved': [], 'skipped': [
#   ('<example file>.pdf',
#    'the record reports <example size> bytes at a filestore path ...'),
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
d.generate_pdf("account.move", record_id)
# /tmp/account_move_<example id>.pdf   <example size> bytes, magic b'%PDF-'
```

For a model with no known wizard, `generate_pdf` raises and tells you what it
found instead of guessing:

```
No known print wizard for project.task. Reports available:
['hr_timesheet.report_project_task_timesheet']. Render them from the UI,
or check the model's binary fields: [...]
```

`d.reports_for(model)` lists what the user sees under **Print**, including any
custom reports installed on that instance.

---

# Part 2 — Notifications

> **The rule that matters: the chatter is not private.** A sampled sales order
> may have a customer among its followers. A `mt_comment` on it emails that
> customer. Before
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
mt_note      -> <example count> notification(s), 0 emails
mt_comment   -> <example count> notification(s), including any external email
```

The second recipient was never requested. It came from the follower list.

## `tell()` — the safe default

`message_notify` under the hood. It reaches the named partners and touches
nothing else:

```python
d.tell("sale.order", sid, "<p>Please check the payment terms.</p>",
       users=staff_user_ids, subject="Check needed")
# notified : [{'partner': '<staff user>', 'via': 'inbox', 'status': 'sent'}]
# emails   : []
# followers: unchanged — the customer heard nothing
```

Use this whenever the message is for staff. `message_post` subscribes people
and fans out to followers; `message_notify` does neither.

## Check the audience first

```python
d.audience("sale.order", sid)
# followers: ['<staff user>', '<external follower>']
# internal : ['<staff user>']
# external : ['<external follower>']
```

"External" means: a follower with no internal user behind it. Those partners
receive every `mt_comment` by email.

## Can the target instance even send email?

```python
d.mail_works()
# servers    : ['<mail server>']
# neutralised: <boolean>
# queue      : {'outgoing': '<example count>', 'sent': '<example count>',
#               'exception': '<example count>'}
# verdict    : "Email is NEUTRALISED on this instance — nothing leaves.
#               Do not tell anyone they were emailed."
```

Odoo neutralises mail on restored databases: the configured mail host may be
an invalid sink.
Messages are marked *sent* into a void. **Check before promising delivery** —
"the customer has been notified" is false on a neutralised instance.

Real failures show up too:

```
state=exception  error='<mail server is archived or unavailable>'
```

An archived mail server silently breaks every outgoing email. Two messages
some messages may remain `outgoing` on the instance — nobody noticed.

## Users and partners are different tables

`message_post` and `message_notify` take **partner** ids, not user ids.
Passing a user id notifies whoever happens to own that partner id, or nobody.
`tell()` and `notify()` resolve `users=[...]` for you.

## Odoo never notifies the author

Ask for two recipients while being one of them and exactly one notification
is created:

```
requested    : [<staff user ids>]
notified     : [{'partner': '<staff user>', 'via': 'inbox'}]
not_notified : [<author user id>]
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
<admin user>        admin           inbox     <admin email>
<staff user>        <login>         inbox     <staff email>
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
           user_id=assignee_id, days=days_until_due,
           note="<p>Check availability.</p>")
# {'summary': 'Call the customer back', 'user_id': [assignee_id, '<staff user>'],
#  'date_deadline': '<example date>'}
```

Activity types are per-instance; several may be bound to a model:

```
<example activity type> · <example activity type> · <example activity type> ...
```

Read `mail.activity.type` instead of assuming; pass `activity_type` when the
type carries meaning.

## Querying `mail.message` can explode

```python
odoo.search_count("mail.message", [])
# Record does not exist or has been deleted. (Record: calendar.event(<example id>,))
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
