# Submission Guide: OpenAI Plugin Directory

Step-by-step instructions for submitting Odoo Assistant to the universal OpenAI Plugin Directory (serving both ChatGPT and Codex). All copy and values referenced below map directly to the dossier in `docs/listing/README.md`.

## Prerequisites

Before submitting to the Plugin Directory, confirm you have:

1. **Verified Developer Identity**: Completed individual or business verification under organization settings at `https://platform.openai.com/settings/organization/general`. **Done** — the organization is verified as a **Business**, under the name **Persevida SL**, which is therefore the developer name the directory will display. The same string is what the hosted server prints on its own legal pages (`ODOO_REMOTE_PUBLISHER`), so portal, privacy notice and terms name one publisher and not three.
   One further prerequisite is decided at the organization level and blocks the submission outright rather than failing review: the project must **not** be on EU data residency, because an EU-residency project cannot submit a plugin carrying an MCP server. Use a global-residency project.
2. **Apps Management Write Role**: Your user role in the OpenAI Platform must have **Apps Management** set to **Write** under `https://platform.openai.com/settings/organization/people/roles` (Organization Owners have this by default).
3. **Prepared Materials**: Have the dossier ready (`docs/listing/README.md`), along with logo assets, starter prompts, and test cases.

## Portal Path

Navigate to the plugin portal:
1. Open [https://platform.openai.com/plugins](https://platform.openai.com/plugins).
2. Click **Create plugin**.
3. Select **With MCP** (for remote MCP-only or MCP with skills).

## Domain Verification

Plugins with MCP must verify ownership of the host domain (`mcp.singleflo.com`):

1. Obtain the verification challenge token from the portal when prompted.
2. In your Coolify environment configuration for `mcp.singleflo.com`, set:
   ```bash
   ODOO_REMOTE_OPENAI_CHALLENGE=<token>
   ```
3. Redeploy the application.
4. Confirm verification by testing the endpoint:
   ```bash
   curl https://mcp.singleflo.com/.well-known/openai-apps-challenge
   ```
   The endpoint must return exactly `<token>` in plain text (this route is landed and verified in `tests/test_remote_app.py`).
5. Click **Verify Domain** in the OpenAI portal.

## Portal Fields (In Portal Order)

### 1. Info Tab

Fill in public listing details from `docs/listing/README.md`:
- **Plugin name**: `odoo-assistant` (from dossier section `Identity` -> `Plugin name`, 64 characters max).
- **Display name**: `Odoo Assistant` (from dossier section `Identity` -> `Display name`, 30 characters max).
- **Short description**: `Odoo ERP via MCP` (from dossier section `Identity` -> `Short description`, 30 characters max).
- **Long description**: Copy exact text from dossier section `Identity` -> `Long description` (4,000 characters max).
- **Developer Identity**: Select your verified developer/business identity.
- **Logo**: Upload production-ready brand assets (from dossier section `Icon`: `docs/listing/icon.png` or `plugins/odoo-assistant/assets/logo.png`).
- **Category**: `Business & Operations` (from dossier section `Categories`).
- **Website URL**: `https://github.com/singleflo/odoo-assistant-mcp` (from dossier section `URLs` -> `Documentation URL`).
- **Support URL**: `https://github.com/singleflo/odoo-assistant-mcp/issues` (from dossier section `URLs` -> `Support URL`).
- **Privacy Policy URL**: `https://mcp.singleflo.com/privacy` (from dossier section `URLs` -> `Privacy URL`).
- **Terms URL**: `https://mcp.singleflo.com/terms` (from dossier section `URLs` -> `Terms URL`).

### 2. MCP Tab

- **MCP Server URL Type**: Universal
- **MCP Server URL**: `https://mcp.singleflo.com/mcp`
- **Authentication**: OAuth 2.0 with PKCE (`oauth_dcr`).
- Click **Scan Tools**.
  - The scan snapshots the server's tool metadata.
  - Snapshot contents: 22 tools (`search_read`, `read_record`, `read_long_field`, `count_records`, `group_records`, `instance_overview`, `required_fields`, `describe_model`, `create_record`, `write_record`, `run_action`, `cancel_record`, `notify_user`, `create_activity`, `download_docs`, `generate_pdf`, `list_message_targets`, `read_conversation`, `send_direct_message`, `send_channel_message`, `explore_module`, `list_known_modules`), along with their `title`, `description`, `inputSchema`, `readOnlyHint`, `destructiveHint`, and `openWorldHint` (refer to dossier section `Tool annotations`).
- The portal asks for a **written justification per annotation**, not one per tool. The dossier's `Tool annotations` table carries the `readOnlyHint` and `destructiveHint` reasoning in its `Why` column, row by row; the `openWorldHint` justification is stated once above the table, because it is the same sentence for all 22 — every tool answers from an operator-supplied Odoo instance rather than from a workspace this publisher owns.

### 3. Prompts Tab

Add 3 starter prompts from dossier section `Starter prompts` (each under 128
chars). ChatGPT prepends the plugin mention itself when it displays them, so
none of the three carries an `@`:

1. `Which quotations are waiting for confirmation this week?`
2. `How many sales orders did we book this month, and what is their total?`
3. `Who can I message in Odoo right now, and who is online?`

The third one used to name a person — "Tell Ana …" — which reads well and
fails on contact: a reviewer who presses it finds no Ana, and the plugin
correctly answers that the recipient does not exist. Correct behaviour, poor
first impression. The replacement exercises the same Discuss surface,
presence included, against whatever users the connected instance actually
has.

### 4. Testing Tab

The tab has three parts: one free-text **Test credentials** box, **exactly 5**
positive cases with four fields each — Scenario, User prompt, Tool triggered,
Expected output — and **exactly 3** negative cases with two fields each,
Description and User prompt.

**A negative case here is not a refusal.** The portal means a prompt the
plugin should NOT be invoked for at all — a near miss the model may think is
relevant. A refusal is the opposite: the plugin correctly invoked, correctly
declining. Putting refusals in these three boxes answers a question nobody
asked, and the cases below are genuine near misses instead. The structural
refusals this server is built around are described in the credentials box, as
behaviour to expect rather than report as a fault.

The copy is in the dossier's `Test cases` section, which is the source of
truth — `tests/test_submit_guides.py` fails when a prompt here and a prompt
there disagree.

#### Test credentials (the free-text box)

There is no password and no Odoo login page, so the placeholder's shape does
not fit. Paste this instead, with the two placeholders filled:

```text
Sign-in URL: none to visit — your client opens the consent page at
https://mcp.singleflo.com/consent when you first use the plugin.
Odoo instance URL: <review instance base URL, no trailing slash>
API key: <fresh key generated for this review>
Database: leave empty (this host serves exactly one database)
Odoo login: leave empty (discovered from the API key)
Password: none — this server accepts an API key only, never a password.

Sign-in steps:
1. Start any of the test cases below. The client opens the consent page.
2. Type the Odoo instance URL and the API key into the two fields.
3. Under "What the assistant may do" choose STANDARD. Cases 4 and 5 write,
   and the read-only choice refuses them by design. Deletion is not offered
   under either choice.
4. Press Connect. There is no MFA, no SMS, no email confirmation and no
   private network: the key authenticates on its own.

One behaviour to expect rather than report as a fault: asking for a total
invoiced amount is REFUSED, naming account.move and asking for an explicit
move_type filter. That Odoo model mixes customer invoices, vendor bills,
credit notes and journal entries, so a total across all of them matches
nothing the user sees on screen. The assistant is expected to re-ask with the
filter and then answer.
```

#### Positive test cases

1. **Quotations awaiting confirmation** — prompt `Which quotations are waiting for confirmation?` → `search_read` on `sale.order` filtered to `state in (draft, sent)` → a short list with number, customer, amount and state; twelve of them on the review instance.
2. **Instance overview** — prompt `Give me an overview of this Odoo instance.` → `instance_overview` → edition and version, companies, record volumes per business area, in-house modules, and an explicit list of what the instance does NOT have.
3. **Totals per bucket in one call** — prompt `How many maintenance tasks are in each stage?` → `group_records` on `project.task` grouped by `stage_id` → one row per stage with its count, seven stages on the review instance, in a single call that never moves the records themselves.
4. **What a create demands, then the create** — prompt `Add a contact called Reviewer Test Partner, email reviewer@example.com.` → `required_fields` on `res.partner`, then `create_record` with `unique_on` → the new partner's id, and the same id again on a second identical request instead of a duplicate.
5. **A message that reaches a colleague** — prompt `Send a direct message to Administrator saying the quarterly report is ready.` → `list_message_targets` then `send_direct_message` → a confirmation carrying recipient, channel and message id, and the delivery route; `read_conversation` then shows it once.

#### Negative test cases

1. **The price of Odoo itself** — prompt `How much does Odoo Enterprise cost per user, and what is included?` The prompt names Odoo, which is what makes it a near miss, but it asks about the vendor's commercial terms rather than the user's records. Nothing in the connected instance can answer it.
2. **Code that talks to Odoo** — prompt `Write me a Python script that connects to Odoo over XML-RPC and lists all contacts.` The strongest near miss in the set: it names Odoo, contacts and a read, and the plugin does all three. The user asked for source code, not for their data.
3. **Figures that are in the conversation** — prompt `Summarise the sales figures in the spreadsheet I just uploaded.` "Sales figures" overlaps exactly with what this plugin reads. The data the user pointed at is in the attachment; reaching into Odoo would replace their numbers with other numbers under the same heading.

### 5. Global Tab

- **Country Availability**: `worldwide` (from dossier section `Country availability`).

### 6. Submit Tab

- **Release Notes**: Copy text from dossier section `Release notes` (Initial submission summary).
- Complete policy attestations and click **Submit for Review**.

## After Approval & Publishing

1. **Review**: OpenAI reviews the submission.
2. **Publish**: Once approved, click **Publish** in the portal.
3. **Universal Directory**: The published plugin appears in the directory for **BOTH ChatGPT and Codex** users automatically.

## Versioning Note

Remote MCP plugins publish a snapshot of reviewed server metadata. If you rename a tool, add tools, or modify schema signatures, you must re-scan the server in the portal, submit a new version for review, and publish the update upon approval.

---

## Sources

- https://developers.openai.com/plugins/deploy/submission
- https://developers.openai.com/plugins/deploy/app-review
- https://developers.openai.com/api/docs/guides/developer-mode
