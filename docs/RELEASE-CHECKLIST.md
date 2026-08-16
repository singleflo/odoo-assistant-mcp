# Release Checklist (User-Only Prerequisites)

This document outlines the manual release prerequisites and verification steps for **Roberto Crotti** to publish the `odoo-assistant` MCP server. 

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
   uv run pytest tests/ -m "not live"
   ```
3. Create and push the version tag:
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```
4. This triggers the `.github/workflows/publish.yml` workflow, which will:
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
3. **Clean Install Verification**: On a clean machine (or in a temporary environment), verify that the package can be executed directly via `uvx`:
   ```bash
   uvx odoo-assistant
   ```
   *(It should prompt for missing Odoo environment variables, confirming the server starts and validates credentials correctly).*
