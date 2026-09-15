# Submission Guide: OpenAI Plugin Directory

Step-by-step instructions for submitting Odoo Assistant to the universal OpenAI Plugin Directory (serving both ChatGPT and Codex). All copy and values referenced below map directly to the dossier in `docs/listing/README.md`.

## Prerequisites

Before submitting to the Plugin Directory, confirm you have:

1. **Verified Developer Identity**: Completed individual or business verification under organization settings at `https://platform.openai.com/settings/organization/general`.
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
  - Snapshot contents: 19 tools (`search_read`, `read_record`, `count_records`, `instance_overview`, `required_fields`, `create_record`, `write_record`, `run_action`, `cancel_record`, `notify_user`, `create_activity`, `download_docs`, `generate_pdf`, `list_message_targets`, `read_conversation`, `send_direct_message`, `send_channel_message`, `explore_module`, `list_known_modules`), along with their `title`, `description`, `inputSchema`, `readOnlyHint`, `destructiveHint`, and `openWorldHint` (refer to dossier section `Tool annotations`).

### 3. Prompts Tab

Add 3 starter prompts from dossier section `Starter prompts` (each under 128 chars):
1. `Which quotations are waiting for confirmation this week?`
2. `How many sales orders did we book this month, and what is their total?`
3. `Tell Ana in a direct message that the quarterly report is ready.`

### 4. Testing Tab

Paste the 5 positive and 3 negative test cases from dossier section `Test cases` and `Reviewer test account (TEMPLATE — the owner fills this before submitting)`:

#### Positive Test Cases:
1. **Quotations awaiting confirmation**: Prompt `Which quotations are waiting for confirmation this week?` -> tool `search_read` on `sale.order`.
2. **Instance overview**: Prompt `Give me an overview of this Odoo instance.` -> tool `instance_overview`.
3. **What a create demands**: Prompt `What do I need to fill in to create a new CRM lead?` -> tool `required_fields` on `crm.lead`.
4. **Create with duplicate reuse**: Prompt `Create a contact named Reviewer Test Partner with the email reviewer@example.com.` -> tool `create_record` on `res.partner` with `unique_on`.
5. **Direct message**: Prompt `Send a direct message to Reviewer Two saying the quarterly report is ready.` -> tools `list_message_targets` then `send_direct_message`.

#### Negative Test Cases:
1. **Delete request**: Prompt `Delete the contact Reviewer Test Partner.` -> refusal: `res.partner.unlink: Deletion is never available on the hosted server; use a local install with ODOO_MCP_ALLOW_UNLINK=yes.`
2. **Cancel on read-only connection**: Prompt `Cancel quotation S00001.` (on read-only) -> refusal: `sale.order.action_cancel: this connection was authorised as read-only; reconnect and choose the standard policy to allow it.`
3. **Invoice query without move_type**: Prompt `What is our total invoiced amount?` -> refusal: `account.move query without an explicit 'move_type' filter.`

Include demo credentials for the reviewer (URL, endpoint `https://mcp.singleflo.com/mcp`, login, API key, database name).

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
