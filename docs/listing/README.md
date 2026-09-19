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
needed. Every one of the five was executed end to end against the review
instance **through the hosted server**, not against a local build, and the
expected results below are what came back.

The two kinds of case mean different things in the OpenAI portal, and
conflating them is how a submission gets marked down. A **positive** case is
a prompt the plugin should answer. A **negative** case is a prompt the plugin
should **not be invoked for at all** — a near miss the model may think is
relevant. It is not a refusal: refusals happen inside a positive case, when
the plugin is correctly invoked and correctly declines.

### Positive test case 1: quotations awaiting confirmation

- Prompt: Which quotations are waiting for confirmation?
- Expected tool: `search_read` on `sale.order`, filtered to the quotation
  states (`state` in `draft`, `sent`).
- Expected result: a short list of quotations — number, customer, amount,
  state — or an explicit "no quotations are waiting" when the list is empty.
  On the review instance this returns six draft quotations.
- Fixture data: at least one `sale.order` in state `draft` or `sent`.

### Positive test case 2: instance overview

- Prompt: Give me an overview of this Odoo instance.
- Expected tool: `instance_overview`.
- Expected result: the Odoo edition and version, the companies, record
  volumes per business area, the in-house modules, and an explicit list of
  what the instance does NOT have — so the assistant says "no inventory here"
  instead of asking the user to clarify.
- Fixture data: none beyond the connected instance.

### Positive test case 3: totals per bucket in one call

- Prompt: How many maintenance tasks are in each stage?
- Expected tool: `group_records` on `project.task`, grouped by `stage_id`.
- Expected result: one row per stage with its count — on the review instance
  seven stages, from 46 in the first to 1 in the last. The point of the tool
  is that this is ONE call: the records themselves never cross the context
  window, only the totals.
- Fixture data: a project with tasks spread over several stages.

### Positive test case 4: what a create demands, then the create

- Prompt: Add a contact called Reviewer Test Partner, email
  reviewer@example.com.
- Expected tool: `required_fields` on `res.partner` to learn what Odoo
  demands and what it would default to, then `create_record` with
  `unique_on` the email.
- Expected result: the new partner's id, and — this is the behaviour worth
  checking — running the same request a second time returns the **same id**
  rather than a duplicate contact.
- Fixture data: none. A contact from an earlier run may already exist; the
  tool reusing it is the expected behaviour, not a failure.

### Positive test case 5: a message that reaches a colleague

- Prompt: Send a direct message to Administrator saying the quarterly report
  is ready.
- Expected tool: `list_message_targets` to resolve the recipient and see who
  is online, then `send_direct_message`.
- Expected result: a confirmation carrying the recipient, the channel and the
  message id, and stating the delivery route — Discuss chat, real time, no
  email, persists while the recipient is offline. `read_conversation` on that
  channel then shows the message, exactly once.
- Fixture data: a second internal user with Discuss enabled. On the review
  instance, `Administrator`.

### Negative test case 1: the price of Odoo itself

- Prompt: How much does Odoo Enterprise cost per user, and what is included?
- Expected tool: none. The prompt names Odoo, which is what makes it a near
  miss, but it asks about the vendor's commercial terms — a question about
  the software, not about the user's own records.
- Expected result: the model answers from general knowledge and the plugin is
  never invoked. Nothing in the connected instance can answer this.
- Fixture data: none.
- Why not: the plugin reads one company's business records. A pricing page is
  not one of them, and invoking it here would spend a tool call to discover
  that.

### Negative test case 2: code that talks to Odoo

- Prompt: Write me a Python script that connects to Odoo over XML-RPC and
  lists all contacts.
- Expected tool: none. This is the strongest near miss in the set: it names
  Odoo, contacts and a read, and the plugin does all three.
- Expected result: the model writes the script. It must not call the plugin:
  the user asked for source code, not for their data, and the script has to
  run against the user's own credentials rather than this connection.
- Fixture data: none.
- Why not: a code-generation request is satisfied by writing code. Reading
  live records would answer a question nobody asked and put real data in an
  answer meant to be a snippet.

### Negative test case 3: figures that are in the conversation

- Prompt: Summarise the sales figures in the spreadsheet I just uploaded.
- Expected tool: none. "Sales figures" overlaps exactly with what this plugin
  reads, which is what makes it tempting.
- Expected result: the model reads the attached file and summarises it. The
  plugin is not invoked, and the numbers in the answer are the ones in the
  attachment — not different numbers pulled from Odoo, which would silently
  answer a different question.
- Fixture data: any spreadsheet attached to the conversation.
- Why not: the data the user pointed at is in the conversation. Reaching into
  Odoo instead would replace their figures with other figures under the same
  heading.

## Reviewer test account (TEMPLATE — the owner fills this before submitting)

Fill every placeholder from a demo user on a non-production instance. Never a
production instance, never a real customer's data. Generate a fresh API key
for the review, give it the longest expiry the Odoo version offers — a key
that lapses mid-review, or during the ongoing testing that follows approval,
kills the reviewer's access with no warning — and revoke it when the review
closes.

**The API key is deliberately not written down here.** It goes into the
submission portal and nowhere else; `tests/test_listing_copy.py` fails if a
40-hex string appears in this file.

| Field | Value |
|---|---|
| Odoo instance URL | `<review instance base URL, no trailing slash>` |
| MCP endpoint | `https://mcp.singleflo.com/mcp` |
| API key | `<fresh key, generated for this review, revoked after>` |
| Database name | leave empty when the host serves exactly one database |
| Odoo login | leave empty when the key owner's uid is below 60 |

**There is no password, and no Odoo login page to visit.** The reviewer signs
in once on the server's own consent page, which their client opens for them,
and types two values: the Odoo URL and the API key. Both optional fields
above are discovered — verified against the review instance, which serves one
database and a key owner well inside the probe range. No MFA, no SMS, no
email confirmation, no private network: the key authenticates on its own.

At that same consent page the reviewer chooses what the assistant may do.
**Pick `standard`** — positive cases 4 and 5 write, and `read` refuses them
by design. Deletion is not offered under either choice.

What the account can see: `<one paragraph — the companies, the draft
quotations, the confirmed sales orders, the customer invoices, the project
tasks across their stages, and the second internal user used for the
messaging case>`.

One behaviour to expect rather than report as a fault: a question like "what
is our total invoiced amount" is **refused**, naming `account.move` and
asking for an explicit `move_type` filter. That model mixes customer
invoices, vendor bills, credit notes and journal entries, so a total over all
of them matches nothing the user sees on screen. The assistant is expected to
re-ask with the filter and then answer.

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
