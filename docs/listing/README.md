# Listing dossier: Claude connectors directory and the OpenAI Plugins Directory

Every field the two store submissions ask for, in labelled sections ready to
paste. Copy is plain on purpose — no superlatives, nothing the tools do not
do — because review teams check claims against behaviour. The hard limits
are enforced twice: once by each store, once by
`tests/test_listing_copy.py`, which parses this file and fails on a size
violation, a missing section, or an annotation table that has drifted from
the live server. Change a field here and the suite tells you if it no longer
fits.

Submission pages: Claude
(https://claude.com/docs/connectors/building/submission) and OpenAI
(https://developers.openai.com/plugins/deploy/submission).

## Identity

### Name

The connector name in Claude's directory (100 characters max).

```text
Odoo Assistant
```

### Plugin name

The plugin name in OpenAI's portal (64 characters max).

```text
odoo-assistant
```

### Display name

OpenAI display name (30 characters max). Already carried by
`plugins/odoo-assistant/plugin.json` as `displayName`.

```text
Odoo Assistant
```

### Tagline

Claude only, 55 characters max.

```text
An Odoo virtual employee — query, create, act, verify
```

### Short description

OpenAI only, 30 characters max. Already carried by
`plugins/odoo-assistant/plugin.json` as `shortDescription`.

```text
Odoo ERP via MCP
```

### Long description

One text for both stores (Claude caps at 2,000 characters, OpenAI at 4,000 —
the shorter limit wins).

```text
Odoo Assistant connects your AI host to your own Odoo instance over the Model Context Protocol. It reads records, creates and updates them, runs workflow actions and reaches your team on the record chatter and in Discuss — and every write passes a method-name gate you own before it reaches Odoo.

Twenty-two tools cover the working day: search, read and count any model; a long text field read in windows; totals per state, stage or month in one grouped call; a field dictionary per model; an instance overview; a required-fields check before any create; record creation that reuses an existing match instead of duplicating; verified writes; workflow actions and cancellation; chatter notes and scheduled activities; document download and PDF rendering; direct and channel messaging; and live module exploration with generated references.

Safety is structural, not advisory. Deletion is refused unless a separate operator variable grants it. The default deny list blocks cancelling, archiving and mass mailing. A query that mixes customer invoices, vendor bills and journal entries is refused, because counting them together produces a number that matches nothing on screen. Every refusal names the call, the rule that decided and the variable that would change the answer — and the lists live in the operator's configuration, read at call time, out of the model's reach.

Authentication uses your Odoo API key, never a password. Works with Odoo 14 through 19, self-hosted, on Odoo.sh and on Odoo Online. Open source under the MIT license.
```

## Categories

Claude asks for one to five categories, picked in the submission portal —
the picker's labels govern, so confirm these against it at submission time:

1. Business
2. Productivity
3. Data & analytics
4. Developer tools
5. Operations

OpenAI takes one category, already carried by
`plugins/odoo-assistant/plugin.json`:

```text
Business & Operations
```

## Starter prompts

Three prompts, each 128 characters or fewer, no @mentions — the highest-value
workflows, specific enough to show when to reach for the connector.

```text
Which quotations are waiting for confirmation this week?
```

```text
How many sales orders did we book this month, and what is their total?
```

```text
Tell Ana in a direct message that the quarterly report is ready.
```

## URLs

### Documentation URL

```text
https://github.com/singleflo/odoo-assistant-mcp
```

### Support URL

```text
https://github.com/singleflo/odoo-assistant-mcp/issues
```

### Privacy URL

```text
https://mcp.singleflo.com/privacy
```

### Terms URL

```text
https://mcp.singleflo.com/terms
```

## Country availability

```text
worldwide
```

## Release notes

Initial submission.

```text
This is the initial submission of the Odoo Assistant plugin.

Odoo Assistant connects ChatGPT and Codex to the user's own Odoo instance over the Model Context Protocol. Twenty-two tools read records, create and update them, run workflow actions, notify colleagues on the record chatter and in Discuss, download documents and render PDFs, and explore the instance's module structure.

Every write passes a method-name gate owned by the instance operator before it reaches Odoo: deletion is refused by default, and a connection can be run read-only. Authentication is by Odoo API key over OAuth 2.1 with dynamic client registration and PKCE; the hosted server stores per-tenant Odoo credentials and session tokens only, described in the privacy policy.

Reviewers: use the test account under the test cases below. The demo data covers quotations, sales orders, one posted customer invoice and two internal users for the messaging tests.
```

## Tool annotations

OpenAI's portal asks for `readOnlyHint`, `destructiveHint` and
`openWorldHint` per tool, with a justification; Claude's portal checks
`readOnlyHint`/`destructiveHint` against the wire. The values below were
dumped from the running server (`list_tools()`), not written by hand — the
test in `tests/test_listing_copy.py` compares this table against the live
annotations and fails when they drift. To regenerate the values:

```bash
uv run python -c "import anyio;from odoo_assistant import server;server._register_all();[print(t.name,t.annotations.read_only_hint,t.annotations.destructive_hint,t.annotations.open_world_hint) for t in anyio.run(server.mcp.list_tools)]"
```

`openWorldHint` is `yes` throughout: every tool answers from an
operator-supplied Odoo instance — an open-ended external system — rather
than from a bounded workspace owned by the publisher.

| Tool | readOnlyHint | destructiveHint | openWorldHint | Why |
|---|---|---|---|---|
| `search_read` | yes | no | yes | Fetches and returns the records matching a domain; nothing it runs can change data, and the answer comes from the customer's own Odoo instance. |
| `read_record` | yes | no | yes | Returns one record's named fields; a pure read whose values depend on the connected instance. |
| `read_long_field` | yes | no | yes | Returns one window of a single text field, so a value larger than the result cap stays readable; a pure read with no side effect. |
| `count_records` | yes | no | yes | Returns how many records match a domain; a pure read with no side effect. |
| `group_records` | yes | no | yes | Runs `read_group` and returns every bucket's count or aggregate in one call; a pure read over the customer's own instance. |
| `instance_overview` | yes | no | yes | Reads version, companies, per-area volumes and installed modules to summarise the instance; its only local artifact is a rebuildable internal cache, so it stays read-only. |
| `required_fields` | yes | no | yes | Asks `fields_get`, `default_get` and existing records what a create would demand; metadata reads only. |
| `describe_model` | yes | no | yes | Asks `fields_get` for a model's field names, types, relations and selection values; metadata read only. |
| `create_record` | no | no | yes | Creates a new record, so it is not read-only, but it overwrites and deletes nothing, and with `unique_on` it reuses an existing match instead of duplicating. |
| `write_record` | no | yes | yes | Overwrites field values on an existing record, so the previous values are lost — destructive even though no record is removed. |
| `run_action` | no | yes | yes | Runs a workflow method that can post, confirm or otherwise transition a record, a state change the record does not undo by itself. |
| `cancel_record` | no | yes | yes | Cancels a record through `action_cancel`, ending its draft or open state. |
| `notify_user` | no | no | yes | Posts a new note on a record's chatter and notifies the users named; the record itself is untouched. |
| `create_activity` | no | no | yes | Schedules a new activity record with a deadline; existing data is untouched. |
| `download_docs` | no | no | yes | Saves every document of a record as a delivered file on the machine running the server — a durable artifact outside Odoo, so it is not read-only; re-running it reproduces the same files and changes no existing data. |
| `generate_pdf` | no | no | yes | Renders a record's report through Odoo's print wizard, which can have side effects such as sending mail, so it is not read-only, though the record itself is unchanged. |
| `list_message_targets` | yes | no | yes | Reads who can be messaged and where, presence included; a pure read. |
| `read_conversation` | yes | no | yes | Returns the messages of one conversation, newest first; nothing is sent or altered. |
| `send_direct_message` | no | no | yes | Posts a new 1-to-1 message, an additive write that alters no existing record. |
| `send_channel_message` | no | no | yes | Posts a new message to an existing channel, an additive write only. |
| `explore_module` | no | no | yes | Interrogates the live instance and writes a persistent reference document to disk — a durable artifact, so it is not read-only; regenerating rewrites the same document, and nothing existing is destroyed. |
| `list_known_modules` | yes | no | yes | Lists the module references this server has already generated, from local files. |

## Test cases

Runnable by a reviewer with the test account below, no internal context
needed. The negative cases name the exact refusal text the server produces —
the reviewer should see those words, not a paraphrase.

### Positive test case 1: quotations awaiting confirmation

- Prompt: Which quotations are waiting for confirmation this week?
- Expected tool: `search_read` on `sale.order`, filtered to the quotation
  states (`state` in `draft`, `sent`).
- Expected result: a short list of quotations — name, customer, amount,
  state — or an explicit "no quotations are waiting" when the list is empty.
- Fixture data: at least one `sale.order` in state `draft` or `sent` in the
  demo database.

### Positive test case 2: instance overview

- Prompt: Give me an overview of this Odoo instance.
- Expected tool: `instance_overview`.
- Expected result: the Odoo version, the companies on the instance, record
  volumes per business area and any installed in-house modules.
- Fixture data: none beyond the connected demo instance.

### Positive test case 3: what a create demands

- Prompt: What do I need to fill in to create a new CRM lead?
- Expected tool: `required_fields` on `crm.lead`.
- Expected result: the required fields with their types and allowed values,
  the default Odoo would apply to each, and how existing records actually
  use them — on the demo instance this surfaces that `crm.lead.type`
  defaults to `lead`.
- Fixture data: none; a handful of existing leads makes the usage
  distribution line meaningful.

### Positive test case 4: create with duplicate reuse

- Prompt: Create a contact named Reviewer Test Partner with the email
  reviewer@example.com.
- Expected tool: `create_record` on `res.partner`, with `unique_on` the
  email.
- Expected result: the partner's id plus a re-read proving the field values;
  running the same request again returns the SAME id, not a duplicate.
- Fixture data: none. A contact from an earlier reviewer's run may already
  exist — the tool reusing it is the expected behaviour, not a failure.

### Positive test case 5: direct message

- Prompt: Send a direct message to Reviewer Two saying the quarterly report
  is ready.
- Expected tool: `list_message_targets` to resolve the recipient, then
  `send_direct_message`.
- Expected result: a confirmation with the message id; opening Discuss as
  Reviewer Two shows the message in the chat systray in real time, with no
  email involved.
- Fixture data: a second internal user named "Reviewer Two" with Discuss
  enabled.

### Negative test case 1: a delete request

- Prompt: Delete the contact Reviewer Test Partner.
- Expected tool: none runs to completion. The connector exposes no delete
  tool; if the model attempts Odoo's `unlink` through a write-path tool, the
  gate refuses before Odoo sees the call.
- Expected result: a refusal naming the deletion policy — on the hosted
  server, exactly: `res.partner.unlink: Deletion is never available on the
  hosted server; use a local install with ODOO_MCP_ALLOW_UNLINK=yes.` The
  contact still exists afterwards.
- Fixture data: the contact from positive test case 4.
- Why not: deletion cannot be undone; the hosted server never grants it, and
  a local install grants it only through a variable the operator sets
  deliberately.

### Negative test case 2: a cancel on a read-only connection

- Prompt: Cancel quotation S00001. (Reviewer is connected under the
  read-only policy.)
- Expected tool: `cancel_record` (attempted).
- Expected result: a refusal naming the connection's policy — exactly:
  `sale.order.action_cancel: this connection was authorised as read-only;
  reconnect and choose the standard policy to allow it.` The quotation stays
  in its state.
- Fixture data: a draft or sent quotation; for this case the reviewer picks
  the read-only policy on the consent page when connecting.
- Why not: the reviewer authorised a read-only connection; the server
  refuses writes instead of silently widening its own mandate.

### Negative test case 3: an invoice query without move_type

- Prompt: What is our total invoiced amount?
- Expected tool: `search_read` or `count_records` on `account.move` without
  a `move_type` filter.
- Expected result: a structural refusal — begins exactly: `account.move
  query without an explicit 'move_type' filter.` and explains that the model
  mixes customer invoices, vendor bills, credit notes and raw journal
  entries, so counting them together produces a number matching nothing on
  screen. A well-behaved model then re-asks with
  `[["move_type", "=", "out_invoice"]]` and returns the customer-invoice
  total.
- Fixture data: at least one posted customer invoice in the demo database.
- Why not: the mixed total is not an error, it is a wrong number — the guard
  refuses a meaningless read the same way it refuses a forbidden write.

## Reviewer test account (TEMPLATE — the owner fills this before submitting)

Fill every placeholder from a dedicated demo user on the development
instance. Never a production instance, never a real customer's data, never a
real person's login. Generate a fresh API key for the review and revoke it
when the review closes.

| Field | Value |
|---|---|
| Odoo instance URL | `<dev instance base URL, no trailing slash>` |
| MCP endpoint | `https://mcp.singleflo.com/mcp` |
| Login | `<demo user login>` |
| API key | `<fresh key, generated for this review, revoked after>` |
| Database name | `<only when the instance serves more than one database>` |

What the account can see: `<one paragraph — the demo companies, the draft
and sent quotations, the sales orders, the one posted customer invoice, the
Reviewer Two user>`.

The connection offers two policies on the consent page: read-only (needed
for negative test case 2) and standard (needed for positive test case 4).
The reviewer connects twice, once per policy. No MFA, email confirmation or
private-network access is involved — the key authenticates on its own.

## Icon

Rendered by `uv run python scripts/make_icon.py` (Pillow, dev dependency
group only): a 512×512 flat PNG with the letters "OA" — no Odoo trademark,
nothing to inflate the wheel. The script writes `docs/listing/icon.png` and
the two copies `plugins/odoo-assistant/assets/icon.png` and `logo.png`,
which `plugins/odoo-assistant/plugin.json` references.

- Claude: the Listing step of the submission portal takes the icon upload —
  https://claude.com/docs/connectors/building/submission
- OpenAI: the Info tab's logo field asks for production-ready brand assets —
  https://developers.openai.com/plugins/deploy/submission

Screenshots are not required: the connector has no user interface beyond the
conversation itself. Claude asks for carousel screenshots only for MCP Apps,
which this connector is not.
