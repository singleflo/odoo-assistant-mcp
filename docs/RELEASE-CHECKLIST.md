# Release Checklist (User-Only Prerequisites)

This document outlines the manual release prerequisites and verification steps for publishing the `odoo-assistant` MCP server from the `singleflo` organization. The package is published on PyPI and listed in the MCP Registry; Trusted Publishing is configured, so Step 1 is done once and the steps that repeat for every release start at Step 4.

These steps require human 2FA, web-UI access, or manual credentials that the automated worker cannot access. **Do not attempt to run these steps via automated agents.**

---

## Step 1: PyPI Project & Trusted Publisher Setup
Before pushing the first release tag, you must configure PyPI to trust the GitHub repository via OpenID Connect (OIDC). This allows GitHub Actions to publish to PyPI securely without storing long-lived API tokens.

1. Log in to your PyPI account at [pypi.org](https://pypi.org/). If you do not have an account, create one.
2. Navigate to the [PyPI Publishing Management page](https://pypi.org/manage/account/publishing/).
3. Add a **Pending Publisher** with the following details:
   - **PyPI Project Name**: `odoo-assistant`
   - **Owner (GitHub Username/Org)**: `singleflo`
   - **Repository Name**: `odoo-assistant-mcp`
   - **Workflow Name**: `publish.yml`
   - **Environment Name**: (Leave blank unless you explicitly configure a GitHub environment for releases)
4. Save the pending publisher. This must be done **before** the first tag is pushed, as Trusted Publishing requires the project to either not exist yet or have the pending publisher pre-registered.

---

## Step 2: GitHub Repository Confirmation
*Note: This step is listed for completeness and audit trail purposes.*

- The GitHub repository `singleflo/odoo-assistant-mcp` has already been created and is public. No further action is required for repository setup.

---

## Step 3: MCP Registry GitHub OIDC Trust
The official Model Context Protocol (MCP) Registry supports GitHub-based authentication using OpenID Connect (OIDC).

- **No manual pre-registration or secret setup is required** on the MCP Registry website or GitHub settings for OIDC.
- The authentication is fully automated in the CI pipeline via the `mcp-publisher login github-oidc` command.
- The workflow in `.github/workflows/publish.yml` is already configured with the necessary permission:
  ```yaml
  permissions:
    id-token: write
  ```
  This permission allows the runner to acquire a temporary OIDC token from GitHub, which `mcp-publisher` uses to authenticate directly with the MCP Registry.

---

## Step 4: Tag and Release
Once PyPI Trusted Publishing is configured, trigger the automated release pipeline by tagging and pushing a release.

1. Ensure the version numbers in `pyproject.toml` and `server.json` are updated and match.
2. Run the local test suite to verify everything is green:
   ```bash
   uv run pytest
   ```
   Do not use the short `-m` expression containing only `not live`: a command-line `-m` replaces `addopts` instead of narrowing it, silently re-enabling the `wheel` marker suite, which fails on a clean checkout because it needs `dist/`.
3. Confirm the "Tests" GitHub Actions workflow is green on the exact commit being tagged. For example, `gh run list --workflow=Tests --branch=main --limit=1` must show success, and its commit SHA must match `git rev-parse HEAD`.
4. Fetch **every** URL in `[project.urls]` and confirm each one both returns 200 **and** actually resolves to the intended content:
   ```bash
   python3 -c "import tomllib;[print(v) for v in tomllib.load(open('pyproject.toml','rb'))['project']['urls'].values()]" \
     | while read -r url; do
         printf '%s -> %s\n' "$url" "$(curl -sL -o /dev/null -w '%{http_code}' "$url")"
       done
   ```
   A 200 is **not** sufficient. Open the response and confirm it names this project: a page can return 200 while silently discarding its query parameters and rendering an unrelated default listing, which is exactly how the 0.1.0 `MCPRegistry` link was mis-diagnosed. Read the body, or load the URL in a browser and look at what renders.

   Do this **before** tagging. `[project.urls]` is baked into the published PyPI metadata and is immutable once a version is released: a broken link can only be corrected by publishing a new version.
5. Create and push the version tag. It must match the version in `pyproject.toml`
   and `server.json`, prefixed with `v` — the workflow triggers on `tags: ["v*"]`:
   ```bash
   VERSION=$(python3 -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
   git tag "v$VERSION"
   git push origin "v$VERSION"
   ```
6. This triggers the `.github/workflows/publish.yml` workflow, which will:
   - Run the test suite.
   - Build the package.
   - Publish the package to PyPI (using Trusted Publisher OIDC).
   - Authenticate to the MCP Registry (using `github-oidc`).
   - Publish the server metadata to the MCP Registry.

---

## Step 5: Post-Publish Verification
After the GitHub Actions run completes successfully, verify the release:

1. **PyPI Verification**: Visit [pypi.org/project/odoo-assistant/](https://pypi.org/project/odoo-assistant/) and verify that the package is live and shows the correct version.
2. **MCP Registry Verification**: Query the MCP Registry API to verify the server is listed:
   ```bash
   curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.singleflo/odoo-assistant"
   ```
   Verify that the returned JSON contains the server metadata.
   PyPI and the MCP Registry can have a short CDN propagation delay after a fresh upload, so the registry can briefly return 404 (see documented MCP Registry issue #553). If the registry job fails immediately after a successful PyPI publish, re-run only the registry job; never bump the version.
3. **Clean Install Verification**: On a clean machine (or in a temporary environment), verify that the package can be executed directly via `uvx`:
   ```bash
   uvx odoo-assistant
   ```
   *(It should prompt for missing Odoo environment variables, confirming the server starts and validates credentials correctly).*
