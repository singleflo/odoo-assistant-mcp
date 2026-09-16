# Privacy Policy

Last updated: 16 September 2026.

This Privacy Policy explains how {{PUBLISHER}} collects, uses, stores, and protects your information when you connect an Odoo instance to an AI client (such as Claude or ChatGPT) using our hosted remote server service.

We operate with strict data minimization principles. Our service functions as an authentication and secure transport bridge between your AI client host and your self-hosted or cloud Odoo instance.

## What We Store

To maintain an active connection and handle authorization securely, our remote server stores a small set of operational data in a SQLite database on our server. The database file itself is not encrypted; specific sensitive fields are encrypted individually, as detailed below.

| Category | Description | Storage Method & TTL |
| --- | --- | --- |
| Odoo URL | The base URL of your target Odoo instance. This is connection metadata, not a secret. | Stored in plaintext in the SQLite database |
| Odoo Database Name | The name of the database your connection targets on that instance. Also connection metadata. | Stored in plaintext in the SQLite database |
| Odoo API Credentials | The Odoo API key provided during authorization (account passwords are never accepted) | Encrypted with Fernet (AES-128-CBC + HMAC-SHA256, via the cryptography package) |
| OAuth Client Records | The dynamic client registration records created when your AI host connects through OAuth 2.1 | Encrypted with Fernet (AES-128-CBC + HMAC-SHA256, via the cryptography package) |
| Policy Selection | The execution policy selected during consent (read-only vs full access) | Stored in SQLite database |
| Hashed Tokens | Cryptographic hashes of OAuth 2.1 authorization codes, access tokens, and refresh tokens | Hashed using SHA-256 (raw tokens are never stored) |
| Tenant References | Generated internal identifiers linking your OAuth subject to your connection settings | Stored in SQLite database |
| Generated References | Module reference documents produced by the exploration tool at your request | Stored as files on disk; kept until your tenant is deleted |
| Temporary Files | Files explicitly downloaded or generated during active tool executions | Stored in temporary storage with a strict 15-minute Time-To-Live (TTL) |

## Why We Store It

Each category above is held for one purpose and for no other:

* The Odoo URL, the database name and the encrypted API key are the connection itself — they are what lets the server reach your instance when your assistant asks it to.
* The policy selection is what the safety gate reads before every write, so that a connection authorised as read-only stays read-only.
* Hashed tokens and tenant references are how a request is recognised as yours rather than another tenant's.
* Generated references and temporary files are the output of the tools you asked to run.

None of it is used for analytics, profiling, advertising, or the training of any model. We run no third-party trackers and the pages of this service load nothing from anyone else.

## Where It Is Processed

One server runs this service, and everything described above stays on it: a single SQLite database file and a directory of generated files on the same machine. Nothing is copied to another provider, to a second region, or to an analytics service. The only outbound request this server makes on your behalf goes to the Odoo address you named during consent.

## What We Never Store

We design our infrastructure to avoid processing or retaining personal or business records beyond what is strictly necessary to proxy requests. We never store:

* Odoo record contents, database tables, or business documents beyond the temporary file TTL needed for active operations.
* Conversation text, prompts, or messages exchanged between you and your AI host.
* Unencrypted API keys, passwords, or raw access/refresh tokens.

## Data Retention and Automatic Purging

We enforce strict data retention rules to ensure connection details and tokens are erased when no longer in use:

* **Temporary Files**: The download link expires 15 minutes after it is issued; the bytes are removed when an expired link is hit, or by the hourly sweep at the latest.
* **Access Tokens**: Short-lived tokens expiring after 1 hour.
* **Refresh Tokens**: Expire after 30 days.
* **Generated References**: Kept until your tenant is deleted.
* **Revocation & Disconnection**: When the last token family for your connection is revoked — which is what disconnecting the integration in your host application (such as Claude or ChatGPT) triggers — the tenant row holding your Odoo connection configuration and credentials is deleted.
* **Idle Purge**: A sweep at server startup and hourly thereafter removes tenant rows that have been idle for 90 days.

## Your Controls

You retain total control over your credentials and active connections. Three of these controls do not involve us at all:

1. **Host Disconnection**: Disconnect or delete the integration directly within your AI host application (e.g., Claude, ChatGPT, or another client). This triggers an automated revocation request to our server.
2. **Explicit Revocation Endpoint**: Send a `POST /revoke` request presenting your active token. Our server immediately revokes the associated token family and erases tenant credentials if no active tokens remain.
3. **Revoke the key inside Odoo**: an API key is revocable from the Odoo account that created it, and revoking it there ends this server's access immediately without going through us. What remains here is an encrypted string that no longer opens anything, deleted with the rest of the tenant row on disconnection or after the idle window.
4. **Change what the connection may do**: sign in again and pick the other policy. The most recent choice governs the connection, including tokens issued before it.

To ask what is held for your connection, or to have it erased before the windows above elapse, write to the contact below and name the Odoo URL you connected; we answer from the same tenant row this policy describes.

## Data Recipients and Third-Party Sharing

We do not sell, rent, or monetize your data. We do not share your connection details or Odoo credentials with any third parties.

The only recipient of requests forwarded by our service is your own target Odoo instance specified during authorization.

## Contact Us

If you have questions, feedback, or concerns regarding this Privacy Policy or data handling practices, please open an issue or reach out to {{SUPPORT_EMAIL}}.
