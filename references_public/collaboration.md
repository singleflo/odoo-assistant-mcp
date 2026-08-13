# Collaboration — notes, activities, calendar

Every record in Odoo carries the same three collaboration layers, and they
are what people actually use to work together:

| Layer | Model | Answers |
|---|---|---|
| chatter | `mail.message` | what was said and done |
| activities | `mail.activity` | what must still be done, by whom, by when |
| calendar | `calendar.event` | when people meet |

```python
import sys; sys.path.insert(0, "scripts")
from odoo_client import connect
from collaboration import Collab

c = Collab(connect(), company_id=target_company_id)
```

The operations below should be verified on the target Odoo instance.

---

# Internal notes

```python
c.note("sale.order", sid, "<p>Payment terms verified.</p>")
# {'posted': True}
c.history("sale.order", sid)          # what has been said here
```

`note()` uses `mail.mt_note`: staff only, **no email to anyone**. The
alternative subtype `mt_comment` emails every follower, and an order may have
a customer among its followers. When in doubt,
this is the safe one.

**Never query `mail.message` without `model` + `res_id`.** Its record rules
re-read the linked record row by row, so a single orphan message kills the
whole query:

```
search_count("mail.message", [])
  -> Record does not exist or has been deleted. (calendar.event(<example id>,))
```

---

# Activities — the only notification with a deadline

A note is passive: it sits there. An activity lands in the assignee's **To
Do**, shows a due date, and flips to `overdue` when it passes.

## Full lifecycle

```python
a = c.todo("sale.order", sid, "Call the customer back",
           user_id=7, days=3, note="<p>Check the discount.</p>",
           type_name="call")
# {'id': '<activity id>', 'summary': 'Call the customer back',
#  'user_id': ['<assignee id>', '<staff user>'],
#  'date_deadline': '<example date>',
#  'state': 'planned'}

c.postpone(a["id"], days=days_to_postpone)
# {'from': '<example date>', 'to': '<example date>', 'changed': True}

c.reassign(a["id"], replacement_user_id)
# {'from': ['<old assignee id>', '<staff user>'],
#  'to': ['<new assignee id>', '<staff user>'], 'changed': True}

c.done(a["id"], "Customer contacted, 10% discount confirmed.")
# {'done': True, 'logged_in_chatter': True, 'record': 'sale.order/<example id>'}

c.drop(other_id)          # cancel, no trace at all
# {'dropped': True, 'trace_left': False}
```

## `done()` vs `drop()` — they are not interchangeable

| | Activity row | Chatter |
|---|---|---|
| `done(id, "outcome")` | deleted | **message describing the outcome** |
| `drop(id)` | deleted | **nothing** |

`action_feedback` deletes the activity and writes the outcome to the chatter
— that message is the only surviving record of the work. The chatter should
Always pass real feedback; an empty one leaves a note
saying nothing.

`drop()` erases it as if it never existed. Use it for mistakes, not for
"we decided not to do this" — in that case `done(id, "cancelled because …")`
keeps the reasoning.

## There is no `action_postpone`

`mail.activity.action_postpone` does not exist. `action_snooze` does, but
shifts by a fixed 7 days. To move a deadline, just write the field.

**The signature trap:** `vals` is the SECOND ARGUMENT of `write`, not a
keyword. Getting it wrong fails with an opaque error that says nothing:

```python
# WRONG — dies with an unreadable dispatch_rpc traceback
odoo.call("mail.activity", "write", [[aid]],
           {"date_deadline": "<example date>", "context": CTX})

# RIGHT
odoo.call("mail.activity", "write", [[aid], {"date_deadline": "<example date>"}],
          {"context": CTX})
```

This cost an hour of debugging Odoo before realising the caller was wrong.
`Writer.write()` and every `Collab` method use the correct form.

## Activity types are per-instance

Activity types are per-instance, several bound to a model:

```
<example activity type> · <example activity type> · <example activity type>
<example model-bound activity type> · <example model-bound activity type>
```

`c.types(model)` lists what applies. `type_name="call"` picks by name; the
default is the first applicable type, which is rarely what you mean.

## Seeing the backlog

```python
c.pending(user_id=assignee_id)
# {'total': '<example count>', 'overdue': '<example count>',
#  'today': '<example count>', 'items': [...]}

c.pending(overdue_only=True)      # what is late
c.workload()
# [{'user': '<staff user>', 'count': '<example count>'}]
```

`state` is computed from the deadline: `planned` · `today` · `overdue`.

---

# Calendar

## The model layout

```
calendar.event        <example count>    the meeting
calendar.attendee     <example count>    one row per invited partner, holds the RSVP
calendar.recurrence   <example count>    the rule behind a repeating series
calendar.alarm        <example count>    reminders (notification or email)
```

`appointment.type` does not exist here — the online booking module is not
installed.

## Creating a meeting with invitations

```python
ev = c.meet("Quarterly review", start, users=invitee_user_ids, minutes=meeting_minutes,
            location="Room A", link_to=("sale.order", sid))
```

`partner_ids` drives everything: Odoo creates one `calendar.attendee` per
partner automatically. **The organiser is `accepted`, everyone else starts at
`needsAction`.**

```
<invitee>             needsAction
<organiser>           accepted
```

## `res_model` is readonly — this one is easy to miss

Linking an event to a record by passing `res_model` **silently does nothing**:

```
create(..., res_model="sale.order", res_id=record_id)
  -> res_model: False, res_model_id: False, res_id: record_id
```

The event has a dangling id and never appears among the record's events.
`res_model` is a computed char field; the writable one is **`res_model_id`**,
a many2one to `ir.model`:

```
write(res_model_id=model_id)
  -> res_model: 'sale.order', res_model_id: [model_id, 'Sales Order'], res_id: record_id
```

`c.meet(link_to=(model, id))` resolves this for you. Inspect the returned
counts to see how many events are linked and to which models.

## RSVPs

```python
c.invite_status(ev["id"])
# {'total': '<example count>', 'accepted': ['<organiser>'], 'declined': [],
#  'tentative': [], 'no_answer': ['<invitee>']}

c.respond(ev["id"], partner_id, "accept")     # accept | decline | tentative
# {'ok': True, 'from': 'needsAction', 'to': 'accepted'}
```

The attendee methods are `do_accept`, `do_decline`, `do_tentative`. All three
verified — state changes immediately, and the transitions are reversible
(declined → accepted works).

Do not read `needsAction` as a refusal; inspect the invitation status before
reporting a meeting as confirmed.

## Recurrences materialise

A weekly meeting is **not** one record. Odoo creates one `calendar.event` per
occurrence, all pointing at the same `calendar.recurrence`:

```
recurrence <example id>  "Every <interval> weeks on <weekday> until <date>"
rrule: DTSTART:<example timestamp>
       RRULE:<example recurrence rule>
  -> <example count> calendar.event rows
```

So editing "the meeting" means deciding whether you mean one occurrence or
the series — `follow_recurrence` on the event controls that. And counting
meetings counts occurrences, not series. Inspect `calendar.recurrence` when
you need to distinguish an occurrence from its series.

## Reminders

```
Notification - <intervals configured on the instance>
Email - <interval configured on the instance>
```

`alarm_type` is `notification` (in-app) or `email`. Attach with
`alarm=<id>` on `meet()`, or write `alarm_ids` afterwards.

## Agenda

```python
c.agenda(days=7)                  # everything scheduled
c.agenda(user_id=2, days=30)      # one person's calendar
```

---

## Safety: what these helpers refuse to do

`Collab` runs every destructive path through the same classifier as the rest
of the skill, so it cannot be used as a side door:

```python
c.drop(activity_id)
# PermissionError: mail.activity.unlink is L4_DESTRUCTIVE.
#   Collaboration helpers refuse destructive calls. Use Writer with
#   confirmed=True, or pass enforce=False deliberately.
```

Deleting an activity or a calendar event has to be a deliberate `Writer` call
with `confirmed=True`. Everything non-destructive passes freely:

| Call | Level |
|---|---|
| `message_post`, `message_notify` | L1 |
| `mail.activity` create / `action_feedback` | L1 |
| `do_accept` / `do_decline` / `do_tentative` | L1 |
| `mail.mail.send`, `action_sendmail` | **L3** — you cannot unsend |
| `unlink` on activity or event | **L4** — refused here |

Sending mail is L3 on purpose: it is not destructive, but it leaves the
building and cannot be recalled.

---

## Before touching the collaboration layer

1. **Note or activity?** If someone must act, it is an activity — a note is
   never a task.
2. **`note()` not `mt_comment`** unless you genuinely mean to write to the
   customer.
3. **`done()` with real feedback**, not `drop()`, whenever the outcome
   matters — the chatter message is the only trace that survives.
4. **Check `invite_status()`** before assuming a meeting is confirmed. Some
   invitations may never be answered.

---

## NOTES — hand-written, preserved across regenerations

Add pitfalls here as you hit them.
