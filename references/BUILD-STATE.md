# BUILD STATE — read this before trusting anything

**Version 1.1.0 · 12 Aug 2026 · 12 cold-start runs + a coherence review**

This file exists so a fresh conversation can pick the work up without the
history that produced it.

## The coherence review (v1.1.0)

After documents, notifications and collaboration were added, the whole skill
was re-read looking for contradictions rather than bugs. Five were found —
none of which any single test would have caught, because each piece worked
on its own:

| Found | Why it mattered | Fixed by |
|---|---|---|
| `action_feedback`, `do_accept`, `mail.send` were **L5_UNKNOWN** | the safety layer BLOCKED the very methods needed to complete an activity or answer an invitation | added to L1; mail sending to L3 |
| `_count_targets` raised `KeyError` on a dict payload | classification died mid-call on any payload-style argument | type-check before indexing |
| `Collab` and `Documents` **never called the safety layer** | a side door: `drop()` deleted activities without meeting L0–L5 | `_guard()` on every destructive path |
| four operations had **two homes** (`Documents.schedule` vs `Collab.todo`, `Documents.chatter` vs `Collab.history`) | the agent picks one at random, and only one of them was maintained | `Documents` now forwards to `Collab`; both kept working |
| 7 references existed but were **cited nowhere** | generated and then unreachable — the agent would never open them | added to the routing table |

**The lesson: integration defects hide between components, not inside
them.** Every module passed its own tests while the safety layer was
silently blocking half the collaboration API.


## What this skill is

An agent that operates an Odoo 18 instance — any instance, including one full
of in-house modules no model has ever seen. It does not rely on knowing Odoo
from training data; it interrogates the instance and writes down what it finds.

## Writing is covered as of 0.6.0

`scripts/write_patterns.py` + `references/writing.md`: twelve patterns, each
discovered by writing to a live instance. The two that matter most, because
both produced **false success reports** in cold-start tests:

- **An exception does not mean nothing happened.** Odoo's XML-RPC serialiser
  fails on methods returning `None` *after committing*. Retrying created
  duplicate invoices.
- **A dict with `res_model` is not a result**, it is "open this wizard".
  `action_cancel` returns one and leaves the order confirmed.

The write test caught the worst defect of the whole project: an agent
reporting **"all 8 tasks completed"** while having created 4 duplicate
customers, written no note, created no invoice, and quoted an invoice from an
unrelated earlier run. Fixed with `unique_on=`, `step()` and `report()`.

## Validation

Nine independent Hermes sessions with zero prior context. Six on standard
questions from a non-technical user, three on **in-house modules only**
(SuperChat, Evolution, 3CX, Odoo Monitor, pre-invoices).

| Run | What broke | Where the fix had to go |
|---|---|---|
| 1 | invented "Helpdesk not installed" (53 tickets existed); ranked customers by `credit_limit` | procedure at the top of SKILL.md |
| 3 | read raw totals as answers: 55 tickets, 79 subscriptions, 2.191 "hours" | **renamed the profile fields** — the reference file was never opened |
| 4 | **€14.314.448 invoiced instead of €1.197.263** — summed `amount_total` across EUR+CZK | **rule in SKILL.md + precomputed total in the profile** |
| 5 | called 1.026 invoices "1.045 entries"; asked to clarify about absent modules | profile lists `NOT_AVAILABLE` |
| 6 | reported 2.037 timesheet *lines* as *hours* | overview shows `timesheet_HOURS` first |
| 7 (custom) | all 10 in-house answers correct, but called an unused Facebook connector an active integration | `installed_but_check_usage` in the profile |
| 8 (custom) | **invented two modules** — `crm_extended`, `accounting_extensions` | **`custom_modules` in the profile**, read from `ir.module.module` |
| 9 (custom) | **29 modules cited, 29 real, 0 invented** | — |
| 10 (write) | claimed "all 8 tasks completed": 4 duplicate customers, note never written, no invoice created, quoted someone else's invoice | `unique_on=`, `report()` |
| 11 (write) | note written, right invoice, no duplicate customer — but **6 orders and 2 orphan draft invoices**: the retry was around the whole step | `step()` |
| 12 (write) | **one customer, one order, one invoice — chain correct.** But reported "state presumibly draft" after a failed read; the invoice was `posted` FT/2026/0062 | `state_of()` with named fields |

**The lesson, learned four times:** a rule only works where it is read. In an
optional reference file it protects nothing — the agent composes its own query
and never opens it. The ranking that emerged:

| Where the fix goes | Holds? |
|---|---|
| rule in an optional reference | ✗ — never opened |
| rule in SKILL.md (always in context) | ✓ |
| encoded in a field *name* (`tickets_OPEN`) | ✓ |
| **the fact itself, in the profile** | ✓✓ — removes the reason to invent |

The last is strongest: it does not ask the agent to remember a rule, it takes
away the gap it would otherwise fill with a guess.

## Scripts — all tested against a live instance

| Script | What it does | Proof |
|---|---|---|
| `odoo_client.py` | one signature over XML-RPC **and** `/json/2`, auto-detected | fixed a real bug: `urlopen` followed the 303 to `/web/login` and returned the login page as success |
| `safety_layer.py` | L0–L5 by effect + structural guards | 5/5: blocks unfiltered `account.move`, `account.move.line`, substring bypass, unconfirmed `unlink` |
| `view_first.py` | composes `domain` + `search_default_*` | "Credit Notes" → 16, not 389 (24,3×) |
| `census.py` | profile in ~0,8 s; `--quick` drift check 0,24 s | 9 KB profile |
| `query.py` | reads slices without touching Odoo | semantic fields shown first |
| `explore_module.py` | **generates a reference by interrogating the instance** | produced the `stage_id.fold` fact that a hand-written recipe had got wrong |

## The self-maintaining part

`explore_module.py` is what makes this expandable to any module, standard or
in-house, without anyone writing documentation by hand.

```bash
python3 scripts/explore_module.py --list                       # priorities, measured
python3 scripts/explore_module.py helpdesk                     # known grouping
python3 scripts/explore_module.py --models a.b,c.d --name x    # anything else
```

Each generated file: volumes per model · real selection values with
distribution · **menus resolved view-first with the error factor** ·
`action_*` methods pulled from the form views · `_signed` fields to sum.

Two halves: everything above `## NOTES` is rebuilt on every run, the NOTES
section is preserved. Re-run after an upgrade and the figures refresh while
hand-written pitfalls survive.

**Size guard:** refuses to write over 12 KB and names the section that blew
the budget. This caught a real failure — `state_id` on `crm.lead` is the
geographic province, and printing every province produced 46 KB of noise in a
50 KB file. Now: 3 KB.

## References present

Generated: `helpdesk` · `superchat` · `evolution` · `odoomon` · `preinvoice` ·
`call3cx` · `api_crm` · `multi_mail` · `fb_leads` · `evo_manager` · `recording`
Hand-written: `recipes.md` (business questions and their traps).

Still worth generating: `accounting`, `projects`, `sales`, `partners`, `crm` —
the five highest-scoring standard areas.

## Reference instance

`persevida_dev18` — Odoo 18 Enterprise, 285 modules, **23 of them in-house
(Persevida S.L.)**, 2 companies (Spain, Czech Republic). Destructive tests
allowed. `api_doc` uninstalled → transport is **XML-RPC**.

Sanity-check figures (12 Aug 2026):

```
invoiced (company currency)  1.197.262,64   <- NOT 14.314.447 (that mixes EUR+CZK)
out_invoice 373 · in_invoice 653 · out_refund 16 · entry 2.433
account.move.line 8.024                     <- largest model on the instance
sale.order 227 total · 119 confirmed · 27 pending · 57 to invoice
partners 177 companies + 430 contacts · 179 customers · 13 users
crm.lead 84 total, 13 active (71 archived)
projects 87 · tasks 1.096 · timesheet 2.037 lines = 5.972 hours
helpdesk 55 ever, 6 open · subscriptions 79 ever, 19 running
custom: superchat 469 msg · evolution 1.537 msg · call3cx 132 calls
        odoomon 4.776 snapshots · preinvoice INSTALLED BUT 0 USED
absent: stock.quant, hr.leave, mrp.production, pos.order
```

## Not done yet

Write paths are now exercised (11 patterns, verified end-to-end: quotation →
confirm → invoice → post → payment → credit note, plus cancellation via
wizard). What remains untested: bank reconciliation (`in_payment` → `paid`),
purchase and inventory flows, and anything on Odoo ≤17.

Also open: OpenCode discovery unverified (binary not in PATH) · per-currency
breakdowns.

## Rule that governs this skill

Nothing enters a reference without the script that produces it and its output.
Three adversarial reviews rejected earlier versions for claims that were
written but never executed — including a "100% coverage" report that summed a
hardcoded list of `True`.
