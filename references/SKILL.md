---
name: odoo
description: "Operate an Odoo 18 instance: query, create, run workflows."
version: 1.6.0
author: Roberto Crotti
license: MIT
metadata:
  hermes:
    tags: [Odoo, ERP, API, Accounting, CRM, Sales, Projects, XML-RPC]
    category: odoo
    requires_toolsets: [terminal]
    config:
      - key: odoo.profile_dir
        description: Directory where per-instance profiles are stored
        default: "~/.hermes/odoo/instances"
        prompt: Odoo instance profile directory
required_environment_variables:
  - name: ODOO_BASE_URL
    prompt: "Odoo base URL — leave empty, pass it per session instead"
    help: "No trailing slash. Include the port if non-standard. Set this only if you always work on ONE instance; otherwise pass --url per call."
    required_for: "nothing; optional default"
    optional: true
  - name: ODOO_API_KEY
    prompt: "Odoo API key — leave empty, pass it per session instead"
    help: "Odoo > avatar > My Profile > Account Security > New API Key. Shown once. Inherits that user's rights and is revocable. Never a password."
    required_for: "nothing; optional default"
    optional: true
  - name: ODOO_DB
    prompt: "Odoo database — leave empty, it is discoverable"
    help: "Auto-discovered when the server exposes the db list. Pass --db to override."
    required_for: "nothing; optional default"
    optional: true
  - name: ODOO_USER
    prompt: "Odoo login — leave empty, the API key identifies its owner"
    help: "Never guess it: a wrong login returns False instead of raising, which looks like a permission error."
    required_for: "nothing; optional override"
    optional: true
---

# Odoo — operate an instance like an experienced user

Query, create, and run cross-module workflows on any Odoo 18 instance
(Community or Enterprise) via API. Built on one non-negotiable principle:
**answer what the user would see in the interface, not what a naive query
returns.**

> **STATUS 1.1.0 — reads, writes, documents, notifications, collaboration. Coherence-reviewed.**
> Working now: `odoo_client.py`, `safety_layer.py`, `view_first.py`,
> `census.py`, `query.py` (all tested against a live instance).
> Read `references/BUILD-STATE.md` for what is done and what is not.

## When to Use

Load this skill for anything touching an Odoo instance: counting or listing
records (invoices, orders, leads, tasks, tickets, partners), creating or
modifying records, running workflows (quotation → order → invoice →
payment), inspecting instance structure, or reporting. Also load it when the
user asks "how many X" about a business system and Odoo is the system of
record.

Do NOT use for Odoo *module development* (Python/XML source in an addon) —
that is ordinary coding work.

## The eight rules that are never negotiable

Each one was learned by getting it wrong against a live instance. The factor
in brackets is how far off the answer was.

**1. Never query `account.move` without `move_type`.**
That model holds customer invoices, vendor bills, credit notes AND raw
journal entries in one table. On the reference instance a filterless count
returns 3.613 records while the user sees 373 customer invoices. Same for
`account.move.line`. The safety layer enforces this structurally.

**2. Reproduce the menu's filter, not just the action's domain.**
An Odoo menu applies `domain` AND `search_default_*` together. Reading only
the domain over-counts — measured 24,3× on "Credit Notes" and 218,7× on
"Vendor Refunds", and the same defect appears on vanilla Community. Always
resolve through the view-first resolver.

**3. Never sum `amount_total` — sum `amount_total_signed`.**
`amount_total` is in the invoice's own currency, so summing it adds euros to
crowns. Measured: **8 foreign-currency invoices out of 376** inflate the
total from €1.200.298 to €14.317.483 — **11,9× on the total, 24,6× on the
top customer** — with nothing in the output looking wrong. Any field with a
`_signed` twin: use the twin.

**4. Always pass the company context on a multi-company instance.**
Counts differ per company. `query.py` lists the companies; pass their ids:

```python
CTX = {"allowed_company_ids": [1, 2]}          # from query.py
odoo.search_count("project.project", [], CTX)  # 87, not 47
```
Without it you silently report one company's figures as the whole business.

**"Paid" needs three checks.** `state` is what the record thinks, `move_id`
whether an entry exists, `is_matched` whether the bank confirmed. Measured:
83 have an entry, **11 are matched** — answering from `state` overstates cash
by 7×. `payment_state` has **seven** values (`reversed`, `blocked` are not
"unpaid"). See `references/payments.md`.

**5. A write is done only when a re-read proves it — and never retry.**
The return value means almost nothing. A call can succeed and change nothing;
it can **raise and have changed everything** (Odoo's serialiser fails on
methods returning `None`, *after* committing); and a **dict with `res_model`
is not a result** — it means "open this wizard", the work is NOT done.

Retrying on those false signals produced, in cold-start tests, 4 duplicate
customers and 6 duplicate orders — and a report claiming 8 completed tasks
when the note was never written and no invoice existed.

```python
from write_patterns import Writer
w = Writer(odoo, company_id=2)
w.create("res.partner", {...}, unique_on=["name"])     # no duplicate record
w.step("order", [["partner_id","=",pid]], "sale.order", # no duplicate chain
       lambda: w.create("sale.order", {...}))
w.state_of("account.move", iid)      # named fields — a bare read() can fail
w.report([("note", "res.partner", [["id","=",pid],["comment","!=",False]], 1)])
#   NOT DONE note: found 0, expected 1
```

**If a query cannot prove a step, it is not done — and never report a state
you did not read.** One run called an invoice "presumably draft" after a
failed read; it was posted, FT/2026/0062. All twelve patterns:
`references/writing.md`.

**6. The chatter is not private — check the audience before writing.**
On the reference instance **12 of 12** sampled sales orders had a customer
among their followers. A `mail.mt_comment` emails every follower, so a note
you think is internal reaches the client. Measured on one order, same
recipients: `mt_note` → 1 inbox notification, 0 emails; `mt_comment` → 2
notifications, **one of them an email to the customer**.

```python
from documents import Documents
d = Documents(odoo, company_id=2)
d.mail_works()                    # is email even enabled? dev DBs neutralise it
d.audience("sale.order", sid)     # external: ['3v di veronesi e vai snc']
d.tell("sale.order", sid, "<p>...</p>", users=[7])   # colleagues only, no email
```

`tell()` (message_notify) reaches the people you name, adds no followers and
emails nobody else. Use `mt_comment` only when you mean to write to the
customer. Details: `references/documents.md`.

**7. Never guess a credential — ask, or discover it.**
Two values are enough: the URL and an **API key**. The database is discovered
from the server, and the login comes from the key itself. Pass them per call:

```bash
python3 scripts/query.py --url http://host:8069 --key <API_KEY>
```

**Never set `--user`/`ODOO_USER` on a hunch.** A wrong login does not raise —
`authenticate()` returns `False`, which reads like a permission problem while
the real cause is an invented username. A cold-start session guessed `admin`,
happened to be right, and reported success.

Ask for an **API key**, never a password: per-user, revocable, and it carries
exactly that account's rights. If the URL or key are missing, **ask** — never
assume an instance.

**8. No write without explicit confirmation, and never on production.**
Reads are free. Writes are confirmed. `action_post` and payments are
irreversible — state the point of no return before crossing it.

## Setup — nothing to configure

**No environment variable is required.** Pass the credentials to any script
as arguments, which is what an MSP needs: several instances in one session,
and no risk of the skill silently talking to the wrong system.

```bash
python3 scripts/query.py --url http://host:8069 --key <API_KEY>
python3 scripts/census.py --url http://host:8069 --key <API_KEY>
python3 scripts/view_first.py "Credit Notes" --url http://host:8069 --key <API_KEY>
```

**Only two values are needed.** The database is discovered from the server
and the login comes from the key itself:

```
base_url    http://dev8069:8069
database    persevida_dev18
login       admin  (resolved from the API key, not supplied)
```

`--db` and `--user` exist as overrides for instances that hide their database
list. You will rarely need them, and `--user` should stay unset — see rule 7.

**Ask the user for an API key, never a password.** If they do not have one:

> Odoo → avatar (top right) → **My Profile** → tab **Account Security** →
> **New API Key** → name it (e.g. "hermes"), confirm with the password, copy
> it. Shown **once**.
>
> Create it under the account whose permissions the work needs — the key
> inherits exactly that user's rights, nothing more.

In your own scripts:

```python
import sys; sys.path.insert(0, "scripts")
from odoo_client import connect_cli
odoo = connect_cli()      # takes --url/--key off sys.argv, else the environment
```

For a single fixed instance you may export them instead, and every script
picks them up with no flags:

```bash
export ODOO_BASE_URL=http://host:8069
export ODOO_API_KEY=...
```

## ALWAYS START HERE

Before answering anything about data, run these two commands. They cost
under a second and prevent the two most common failures.

```bash
cd <skill directory>
python3 scripts/census.py --quick    # is the profile current?
python3 scripts/query.py             # what does this instance hold?
```

The overview tells you which areas EXIST and their volumes. Most questions
are answered from it directly, with no further calls.

**Never conclude a module is missing because a query failed.** A failed
query means a wrong model name, a permission issue, or a typo far more often
than a missing module. The profile lists what exists — check there. Saying
"Helpdesk is not installed" when 53 tickets exist is worse than saying
nothing.

## Quick Reference

Run from the skill directory (`cd` there first). Every script accepts
`--url` and `--key`; add them once per command if the environment is not set.

| Task | Command |
|---|---|
| Who am I talking to? | `python3 scripts/odoo_client.py` |
| **Start here:** what does this instance hold? | `python3 scripts/query.py` |
| Did anything change? (0.2 s) | `python3 scripts/census.py --quick` |
| Build / refresh the profile (~1 s) | `python3 scripts/census.py` |
| One area in detail | `python3 scripts/query.py accounting\|sales\|crm\|projects\|helpdesk\|partners` |
| Real names of journals, stages, teams | `python3 scripts/query.py vocabulary` |
| Overdue, drafts, orphans | `python3 scripts/query.py anomalies` |
| **What does this menu really show?** | `python3 scripts/view_first.py "Credit Notes"` |
| All actions on a model | `python3 scripts/view_first.py --model account.move` |
| **Write patterns, live demo** | `python3 scripts/write_patterns.py` |
| **PDF / attachments / notifications** | `python3 scripts/documents.py` |
| **Activities, notes, calendar** | `python3 scripts/collaboration.py` |

For anything not covered by the profile, write a short Python script that
imports the client — never raw HTTP:

```python
import sys; sys.path.insert(0, "scripts")
from odoo_client import connect_cli
from safety_layer import safe_call, SafetyViolation

odoo = connect_cli()          # --url/--key from the command line, or the env
CTX = {"allowed_company_ids": [1, 2]}          # from query.py
rows = odoo.search_read("account.move",
        [["move_type", "=", "out_invoice"], ["state", "=", "posted"]],
        ["name", "partner_id", "amount_total_signed"],   # _signed, see rule 3
        limit=10, order="amount_total_signed desc", context=CTX)
```

Writes go through `Writer`, which verifies the effect instead of trusting
the return value. The safety layer classifies every call (L0–L5) underneath:

```python
from write_patterns import Writer
w = Writer(odoo, company_id=2)          # company that can invoice

ok, why = w.can("sale.order", "action_confirm", sid)   # ask before acting
r = w.act("sale.order", "action_confirm", sid, watch="state")
print(r)          # <sale.order.action_confirm state: 'draft' -> 'sale' CHANGED>
print(w.summary())                       # flags calls that changed nothing

safe_call(odoo, "res.partner", "create", [{"name": "ACME"}], confirmed=True)
```

> Paths are **relative to this skill's directory** — portable across Hermes,
> Claude Code and OpenCode. `cd` into it first, or prefix with the absolute
> path your agent reports when loading the skill.

## Missing a reference? Generate it — never guess

References are **produced by interrogating the instance**, not written from
memory. This is not a stylistic preference: a hand-written recipe in this very
skill used `stage_id.is_close`, a field that does not exist in Odoo 18, and it
survived until a live test caught it.

```bash
python3 scripts/explore_module.py --list      # what exists here, with priority
python3 scripts/explore_module.py helpdesk    # -> references/helpdesk.md
python3 scripts/explore_module.py --models superchat.message,superchat.template --name superchat
```

The last form works on **any** module, including in-house ones no model has
ever seen — the facts come from `fields_get`, `read_group` and the real menu
tree, never from training data.

Each generated file has two halves:

| Part | Behaviour |
|---|---|
| everything above `## NOTES` | rebuilt on every run — never edit by hand |
| `## NOTES` at the bottom | your pitfalls, **preserved** across regenerations |

After an Odoo upgrade or a new module, re-run it: the figures refresh, your
notes survive. If it refuses because the output exceeds the size budget, pass
fewer models — a reference too large to open is worse than none, because the
agent will skip it and guess instead.

## Which module does what

Nine scripts, four jobs. Pick by what you are doing, not by what you are
looking at:

| I want to… | Module | Entry point |
|---|---|---|
| ask a question about the data | `query.py` / `census.py` | `query.py <area>` |
| know what a menu really shows | `view_first.py` | `resolve_menu_domain()` |
| change business data | `write_patterns.py` | `Writer` |
| talk to people, plan work | `collaboration.py` | `Collab` |
| get files in or out | `documents.py` | `Documents` |
| check a call is allowed | `safety_layer.py` | `safe_call()` / `classify()` |
| document a module | `explore_module.py` | `--list`, then the name |

**One job, one home.** Notes, activities and meetings live in `Collab`;
`Documents.schedule()` and `Documents.chatter()` still work but only forward
there. Business writes go through `Writer` — it verifies effects and blocks
duplicate chains. `Collab` refuses destructive calls outright: deleting an
activity or an event has to be a deliberate `Writer` call with
`confirmed=True`.

## Routing — load only what the question needs

This file is the router. Open **one** reference with
`skill_view("odoo", "references/<file>")`:

| The user asks about | Load |
|---|---|
| instance state, drift, new instance, credentials | `census.py --quick`, then this file |
| any count, "how many", a menu figure | `view_first.py "<menu name>"` |
| creating / modifying / posting anything | **`writing.md`** — the seven write patterns |
| invoices, payments, credit notes, journals, taxes | `accounting.md` |
| quotations, sales orders, products | `sales.md` |
| projects, tasks, timesheets | `projects.md` |
| customers, contacts, companies | `partners.md` |
| leads, opportunities, pipeline | `crm.md` (structure) · **`crm_leads.md`** (lead vs opportunity, conversion) |
| **converting a lead — it creates customers** | **`crm_leads.md`** |
| subscriptions, renewals, churn | `recipes.md` |
| tickets, SLA, helpdesk teams | `helpdesk.md` |
| in-house module (messaging, telephony, monitoring, lead intake…) | `superchat.md` · `evolution.md` · `call3cx.md` · `odoomon.md` · `preinvoice.md` · `api_crm.md` · `fb_leads.md` · `multi_mail.md` · `evo_manager.md` · `recording.md` |
| payments, reconciliation, "is it actually paid?", Odoo 17→18 field renames, Italian SDI | **`payments.md`** |
| **deleting a company or bulk data** | **`deletion.md`** — read before touching |
| **PDF, print, attachments, chatter files** | **`documents.md`** |
| **notifying a user, emails, who receives what** | **`documents.md`** |
| **internal notes, activities, to-dos, deadlines** | **`collaboration.md`** |
| **meetings, invitations, RSVPs, recurrences** | **`collaboration.md`** |
| **any business question** (revenue, top customers, overdue, late tasks, open tickets) | **`recipes.md` — start here** |
| a module with no reference yet | `explore_module.py --list`, then generate |

Run `ls references/` to see what exists on this installation — the list above
is what was generated for the development instance, not a fixed set.

## Procedure

1. **Know the instance.** `census.py --quick`. If no profile exists, or the
   fingerprint differs from the stored one, say so and offer `census.py`.
   Never operate on a stale map silently.
2. **Answer from the profile when possible.** It holds volumes, top entities,
   distributions and the real vocabulary. Prefer it over exploring.
3. **For a figure, go view-first.** Resolve the menu the user means, then
   count. Never invent a domain.
4. **No reference for the module?** Generate one — do not guess.
5. **For a write, state the effect first**, then wait for confirmation.
6. **Verify after acting.** Re-read the record. If the after-state equals the
   before-state, the action did NOT take effect — do not report success.

## Pitfalls

- **`create()` returns a list**, not an int.
- **Methods starting with `_` are always rejected.** Use the public wizard
  (`sale.advance.payment.inv`, not `_create_invoices`).
- **XML-RPC needs `allow_none=True`**, or a domain containing `None`/`False`
  raises `TypeError: cannot marshal None`.
- **Selection keys are often prefixed** (`1_draft`, `3_progress`). Read them
  with `fields_get`, never guess.
- **`active_test`**: archived records are hidden by default — 84 leads exist,
  a plain query returns 13.
- **`crm.lead` holds two things.** `type='lead'` (raw) and
  `type='opportunity'` (qualified) live in the same table; a query without
  `type` answers neither question. And **converting a lead CREATES customer
  records** — up to two, and with no contact fields it names one after the
  lead's title. See `references/crm_leads.md` before converting anything.
- **Installed ≠ used.** A module can be present with zero data. Check before
  calling a capability active.
- **Registering a payment**: use the `account.payment.register` wizard;
  creating `account.payment` directly does NOT reconcile.
- **`invoice_policy=delivered`** blocks invoicing until delivery is recorded.

## Verification

- A count is trustworthy only if the domain came from a resolved menu.
- A write is done only if a fresh read confirms the new state.
- A subagent's report is a claim, not a fact — verify it externally.
- The instance is known only if the fingerprint matches the stored profile.
