# The hosted server: a developer guide

`odoo-assistant-remote` is the same package's second front door: one process
serving the same 19 tools over streamable HTTP at `/mcp`, with its own OAuth
2.1 authorization server in front of them. Instead of reading one instance's
credentials from the environment, every request is bound to the person signed
in — a tenant — whose Odoo URL and API key they entered on the consent page.

The user-facing side of this story is in the README, under "Hosted server:
Claude.ai, ChatGPT and Codex". This guide is for running and deploying that
server yourself: locally, through a tunnel, and on Coolify.

## Run it locally

```bash
uv sync --extra remote
export ODOO_REMOTE_PUBLIC_URL=http://127.0.0.1:8000
export ODOO_REMOTE_SECRET_KEY=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
uv run odoo-assistant-remote
```

This listens on `localhost:8000`. The variables:

| Variable | Required | Meaning |
|---|---|---|
| `ODOO_REMOTE_PUBLIC_URL` | Yes | The address clients connect to. Must be an `https://` URL (or `http://localhost` / `http://127.0.0.1` for local runs). Everything the OAuth flow advertises — issuer, protected-resource metadata, consent and token URLs — is derived from it, character for character. |
| `ODOO_REMOTE_SECRET_KEY` | Yes | A Fernet key. It encrypts every tenant's Odoo API key at rest; see "Rotating the secret" below. |
| `PORT` | No | Listen port. Defaults to the public URL's own port when it carries one, else 8000. |
| `ODOO_REMOTE_HOST` | No | Bind address, default `0.0.0.0`. |
| `ODOO_REMOTE_ALLOW_PRIVATE_TARGETS` | No | `1` lets a consent form point at a private Odoo address — see "A dev Odoo on a private address". |

If the process starts with `ODOO_BASE_URL`, `ODOO_API_KEY`, `ODOO_DB` or
`ODOO_USER` still in the environment, it refuses to start: the hosted server
is multi-tenant and must never lend one tenant's credentials to the rest.

Check it came up:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok","version":"…"}
```

Keep host and `ODOO_REMOTE_PUBLIC_URL` consistent. The server refuses requests
whose `Host` header does not match the public URL (DNS-rebinding protection),
so pointing the client at `127.0.0.1:8000` while the server advertises
`localhost:8000` — or the other way round — answers 421 and looks like a
protocol failure. It is a spelling mismatch.

## Get a token: scripts/remote_token.py

The token script walks the whole flow a host walks — dynamic client
registration, PKCE, `/authorize`, the consent page (which is the step that
stores your Odoo credentials as a tenant), the code exchange — and prints the
access token:

```bash
python3 scripts/remote_token.py --help
export ODOO_REMOTE_TEST_ODOO_URL=https://your-dev-odoo.example.com
export ODOO_REMOTE_TEST_API_KEY=your-dev-api-key
export ODOO_REMOTE_TEST_DB=your-dev-db
python3 scripts/remote_token.py --policy read
```

`--policy` is the read-or-standard choice the consent page offers: `read`
(default) or `standard`, which can write. The server URL comes from
`ODOO_REMOTE_PUBLIC_URL`, or `--url` overrides it.

**Test against a dev Odoo only.** A `standard` token can create records and
run workflow actions on whatever instance the consent form named. Never point
this at a production instance; the dev instance is disposable by design.

## Verify with MCP Inspector

From the repository root:

```bash
npx @modelcontextprotocol/inspector --cli http://127.0.0.1:8000/mcp \
  --transport http \
  --header "Authorization: Bearer <token>" \
  --method tools/list
```

Nineteen tools come back, each with a `title` and store-grade annotations.
The JSON wraps them as `{"result":{"tools":[…]}}`.

Three Inspector CLI traps, all measured on this machine:

* **Exported variables do not reach the server.** The CLI does not propagate
  the parent environment to a server it launches; `export ODOO_…=…` looks
  right and arrives empty, which reads like a server bug. When the Inspector
  starts the server itself (a stdio command), pass values as `-e KEY=value` on
  the Inspector command line. The HTTP invocation above has no child process —
  the token goes in the `--header`.
* **Never pass `--directory`.** Any flag name the Inspector itself defines is
  consumed by the Inspector instead of forwarded, so `--directory .` is eaten
  and the target silently mis-reads. That is why the invocation runs from the
  repository root.
* **`uvx odoo-assistant@0.2.0` fails misleadingly.** uv rejects the
  `pkg@version` spelling with "no version … unsatisfiable" although the index
  serves that version. `uvx odoo-assistant` works. When a version pin fails,
  suspect the spelling before the package.

## Expose it with a tunnel

Claude.ai and ChatGPT can only reach public HTTPS addresses. To test the local
server from a real host, put a tunnel in front of it:

```bash
cloudflared tunnel --url http://localhost:8000
# … https://something-random.trycloudflare.com
export ODOO_REMOTE_PUBLIC_URL=https://something-random.trycloudflare.com
uv run odoo-assistant-remote
```

Start the tunnel first: it prints the URL you must then set. **`ODOO_REMOTE_PUBLIC_URL`
must equal the tunnel URL exactly.** The issuer URL and the protected-resource
metadata (RFC 9728) are derived from that variable, and a host authenticating
against the tunnel fetches `/.well-known/…` from it and compares what it
advertises with the URL it is talking to — an exact match, no normalization.
If the server advertises `http://127.0.0.1:8000` while the client is on the
tunnel, the failure is a protected-resource metadata (PRM) mismatch and reads
like an authentication bug. The fix is to restart the server with the variable
pointed at the tunnel URL, which is why the troubleshooting list predicts this
error here: check that the two URLs match before debugging anything else.
`ngrok http 8000` works the same way.

A tunnel exposes your local server to the public internet. Authentication
stays on — it is not optional in this server — and the tunnel should be shut
down when you are done testing.

## A dev Odoo on a private address

By default the consent page refuses an Odoo URL that resolves to a private or
loopback address. The server is about to hold credentials and make
authenticated connections on a stranger's instruction — without that check,
any signed-in tenant could aim it at your internal network. A development
Odoo on `192.168.x.x`, `10.x.x.x` or `localhost` therefore needs:

```bash
export ODOO_REMOTE_ALLOW_PRIVATE_TARGETS=1
```

Set it for local testing and never in production: on the deployed server that
variable is the door to everything behind the proxy, and no legitimate tenant
is on a private address.

## Connect from the hosts

The host-side snippets live in the README and are repeated here so this guide
is complete on its own. All of them point at the deployed server; for a
tunnel, substitute the tunnel URL.

* **Claude.ai** — Customize → Connectors → Add custom connector, URL
  `https://mcp.singleflo.com/mcp`; the first use lands on the consent page.
* **ChatGPT** — Settings → Security and login → Developer mode, then create a
  developer-mode app at chatgpt.com/plugins for the same URL.
* **Claude Code** — `claude mcp add --transport http odoo-assistant https://mcp.singleflo.com/mcp`
* **Codex** — `codex mcp add odoo-assistant --url https://mcp.singleflo.com/mcp`
  then `codex mcp login odoo-assistant`

## What the server keeps, and for how long

One SQLite database, `remote.db` under the server's data directory, holds the
OAuth artifacts and the tenants. Per tenant: the Odoo URL, the API key —
encrypted with `ODOO_REMOTE_SECRET_KEY`, never stored in the clear — and the
policy chosen at sign-in. Sign-in tokens are stored only as SHA-256 hashes.

The policy is what the gate enforces for that person's requests: `read` admits
reads only; `standard` admits reads and writes, still under the default deny
list. `unlink` is not reachable through a tenant under either choice — hosted
connections never delete, whatever the conversation asks for.

Files a tool produces (`download_docs`, `generate_pdf`) never come back as
blobs. They are moved under the tenant's files area and answered with a link
`…/files/<token>` that serves the bytes for **fifteen minutes**; the token is
the credential, an expired link answers 404 and deletes the file, and the
response carries `Cache-Control: private, no-store` so no shared cache keeps a
tenant's invoice. Tenants idle for 90 days are purged at startup.

## Rotating the secret

`ODOO_REMOTE_SECRET_KEY` encrypts every stored tenant credential. Rotating it
— generate a new Fernet key and restart — makes every stored Odoo API key
undecryptable, so every tenant must sign in again through the consent page.
There is no migration path, by design: a key that could decrypt under its
successor would not be worth rotating. Refresh tokens issued before the
rotation keep working until their own expiry; reconnecting replaces them.

## Deploying to Coolify

The deployed instance serves `https://mcp.singleflo.com`. Deploy by pushing a
tag — the CI workflow builds and deploys it — or press **Redeploy** in Coolify
for the same commit.

Then check health **first**:

```bash
curl https://mcp.singleflo.com/health
```

While the container is starting or failing its health check, Coolify's proxy
answers `No available server` as plain text. That is the proxy speaking, not
an error from the application — there may be nothing listening yet, and the
deploy logs, not an HTTP status, tell you which. Once the container is up, the
same URL returns `{"status":"ok","version":"…"}`.

One configuration rule binds this document to `server.json`: on the deployed
instance, `ODOO_REMOTE_PUBLIC_URL` must be `https://mcp.singleflo.com`,
character for character — the registry entry's remote URL is that plus `/mcp`,
and the two are only correct together.

## Store submission

The listing dossier — public copy, starter prompts, test cases, annotation
justifications, icon — and the per-directory submission guides live in
`docs/listing/`. Submitting to the Claude directory or the ChatGPT Plugin
Directory is a human step; the guides walk it, the agents stop at preparing
everything up to the click.

## Sources

* https://claude.com/docs/connectors/custom/remote-mcp
* https://claude.com/docs/connectors/building/directory-vs-custom
* https://claude.com/docs/connectors/building/testing
* https://developers.openai.com/api/docs/guides/developer-mode
* https://developers.openai.com/codex/cli/reference
* https://code.claude.com/docs/en/mcp
* https://modelcontextprotocol.io/registry/remote-servers
