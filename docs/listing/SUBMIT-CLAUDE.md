# Submission Guide: Claude Connectors Directory

Step-by-step instructions for submitting Odoo Assistant to the Anthropic Connectors Directory. All copy and values referenced below map directly to the dossier in `docs/listing/README.md`.

## Prerequisites

Before submitting to the Connectors Directory, confirm you have:

1. **A Team or Enterprise organization** on Claude.ai (organization settings are not available on individual plans).
2. **Directory management access**: You must be an organization Owner or Primary owner. On Enterprise plans, an Owner can delegate this by creating a custom role in **Organization settings > Roles** with either the **Directory** or **Libraries** permission and assigning it to your account (as cited in Anthropic's "Before you start" documentation).
3. **Prepared materials**: Have the dossier ready (`docs/listing/README.md`), along with the icon file `docs/listing/icon.png` and a populated reviewer test account on a development instance.

## Portal Path

Navigate to the submission portal:
1. Sign in to [Claude.ai](https://claude.ai).
2. Open **Admin settings** (or **Organization settings**).
3. Select **Directory** -> **Submissions** -> **New submission** (portal path: `https://claude.ai/admin-settings/directory/submissions/new`).

## Portal Fields (In Submission Portal Order)

The portal walks through sequential steps. Fill each field using the values from `docs/listing/README.md`:

### Step 1: Introduction
- Select **Remote MCP server**.

### Step 2: Connection
- **Server URL**: `https://mcp.singleflo.com/mcp`
- **Transport**: Streamable HTTP
- **URL Reach**: Universal URL (the same URL for every user).

### Step 3: Tools
- Select **Sync Tools**. The portal automatically reads the 22 tools exposed by `https://mcp.singleflo.com/mcp` and verifies that every tool carries a title and valid annotations. Claude requires `title` plus one of `readOnlyHint` / `destructiveHint`; this server publishes all three hints on all 22, which also satisfies OpenAI's stricter rule.

### Step 4: Listing
- **Name**: `Odoo Assistant` (from dossier section `Identity` -> `Name`, 100 characters max).
- **Tagline**: `An Odoo virtual employee — query, create, act, verify` (from dossier section `Identity` -> `Tagline`, 55 characters max).
- **Description**: Copy the exact text from dossier section `Identity` -> `Long description` (2,000 characters max).
- **Categories**: Select 1 to 5 categories from the portal dropdown (recommended from dossier section `Categories`: Business, Productivity, Data & analytics, Developer tools, Operations).
- **Documentation URL**: `https://github.com/singleflo/odoo-assistant-mcp` (from dossier section `URLs` -> `Documentation URL`).
- **Privacy Policy URL**: `https://mcp.singleflo.com/privacy` (from dossier section `URLs` -> `Privacy URL`).
- **Support Contact / URL**: `https://github.com/singleflo/odoo-assistant-mcp/issues` (from dossier section `URLs` -> `Support URL`).
- **Country availability**: `worldwide` (from dossier section `Country availability` — the portal asks where the connector is available).
- **Icon**: Upload `docs/listing/icon.png` (from dossier section `Icon`, 512x512 flat PNG).
- **URL Slug**: Set the listing URL slug.
  > **Warning:** The URL slug is permanent once published and cannot be changed later.

### Step 5: Use cases
- Describe primary workflows based on dossier section `Starter prompts` and `Release notes`. Indicate that the connector performs both read and write operations under safety enforcement.

### Step 6: Company
- Enter company name, website (`https://singleflo.com`), and primary contact email.

### Step 7: Authentication
- **Authentication Choice**: OAuth with Dynamic Client Registration (`oauth_dcr`).
  - Our OAuth metadata advertises `S256` PKCE and `registration_endpoint` (measured and verified in `tests/test_remote_auth.py`).

### Step 8: Data handling
- Confirm first-party Odoo integration via external API, without handling personal health data or sponsored content.

### Step 9: Test & launch
- Paste the completed credentials block from dossier section `Reviewer test account (TEMPLATE — the owner fills this before submitting)`:

| Field | Value |
|---|---|
| Odoo instance URL | `<dev instance base URL, no trailing slash>` |
| MCP endpoint | `https://mcp.singleflo.com/mcp` |
| Login | `<demo user login>` |
| API key | `<fresh key, generated for this review, revoked after>` |
| Database name | `<only when the instance serves more than one database>` |

Include a summary of visible fixture data: the companies, the draft quotations, the confirmed sales orders, the customer invoices, the project tasks spread across their stages, and the second internal user the messaging case writes to.

- Confirm you have tested all 22 tools using MCP Inspector or custom connectors.

### Step 10: Compliance
- Attest to all seven policy acknowledgments.

### Step 11: Review & Submit
- Conduct final verification of submitted fields and click **Submit for Review**.

## What Reviewers Check

During review, Anthropic verifies:
1. **Tool Annotations**: Every tool has a `title`, `readOnlyHint`, `destructiveHint`, and `openWorldHint` matching actual execution behavior (refer to dossier section `Tool annotations`).
2. **Privacy Page**: The privacy URL (`https://mcp.singleflo.com/privacy`) is live, HTTPS, accessible, and discloses data storage practices.
3. **Working Examples**: Test cases exercise real tools (e.g. `search_read`, `instance_overview`, `required_fields`, `create_record`, `send_direct_message`).
4. **Test Account**: Reviewer credentials work cleanly without MFA, SMS, email verification, or private network barriers.

## What Happens After Submission

Submission is no longer one human gate. An automated scan runs first, and a
server that passes it is listed as a **Community Connector** without anyone
testing it by hand. Anthropic may then escalate a connector it considers
highly useful to **Verified**, a slower review where a person exercises every
tool. The two labels differ in the signal they give a user, not in what the
connector may do once connected — so the listing is live at the first
outcome, and the badge is a second, separate event that is not applied for.

- **Submissions Dashboard**: Track progress and reviewer messages at `https://claude.ai/admin-settings/directory/submissions`.
- **Publication**: Once approved and published, your listing slug becomes permanent and public.
- **No domain verification**: unlike OpenAI, Anthropic asks for no DNS record
  and no `.well-known` challenge here. The `/.well-known/openai-apps-challenge`
  route this server carries is for the OpenAI submission alone.
- **Health & Usage Dashboard**: Access server health metrics and invocation volume analytics from the dashboard.

## Custom Connector Fallback (While Waiting)

While waiting for directory approval, users can connect immediately using the custom connector link (percent-encoded URL from dossier/README):

```text
https://claude.ai/customize/connectors?modal=add-custom-connector&connectorName=Odoo%20Assistant&connectorUrl=https%3A%2F%2Fmcp.singleflo.com%2Fmcp
```

## Versioning Note

If you modify server tool names, add tools, or alter metadata schemas, you must update the server deployment and submit an updated version through the portal.

---

## Sources

- https://claude.com/docs/connectors/building/submission
- https://claude.com/docs/connectors/building/directory-vs-custom
- https://claude.com/docs/connectors/building/authentication
