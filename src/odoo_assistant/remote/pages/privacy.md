# Privacy Policy for odoo-assistant Hosted Remote Server

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

## How to Revoke Access

You retain total control over your credentials and active connections. You can revoke access at any time through either of the following mechanisms:

1. **Host Disconnection**: Disconnect or delete the integration directly within your AI host application (e.g., Claude, ChatGPT, or another client). This triggers an automated revocation request to our server.
2. **Explicit Revocation Endpoint**: Send a `POST /revoke` request presenting your active token. Our server immediately revokes the associated token family and erases tenant credentials if no active tokens remain.

## Data Recipients and Third-Party Sharing

We do not sell, rent, or monetize your data. We do not share your connection details or Odoo credentials with any third parties.

The only recipient of requests forwarded by our service is your own target Odoo instance specified during authorization.

## Contact Us

If you have questions, feedback, or concerns regarding this Privacy Policy or data handling practices, please open an issue or reach out to {{SUPPORT_EMAIL}} (defaults to https://github.com/singleflo/odoo-assistant-mcp/issues).
