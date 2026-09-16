# Support

Welcome to the support page for the odoo-assistant remote server integration. {{PUBLISHER}} provides technical assistance and resources for setting up and troubleshooting your integration.

Our hosted remote server acts as an authentication bridge between your AI client host (such as Claude or ChatGPT) and your Odoo instance. We maintain this service to enable secure OAuth 2.1 access and proxy MCP operations seamlessly.

## Support Resources

If you encounter issues, discover bugs, or have feature requests, please use the following official channels:

* **Issue Tracker**: [https://github.com/singleflo/odoo-assistant-mcp/issues](https://github.com/singleflo/odoo-assistant-mcp/issues)
* **Documentation**: [https://github.com/singleflo/odoo-assistant-mcp#readme](https://github.com/singleflo/odoo-assistant-mcp#readme)

## Service Response Expectations

Support for this project is provided on a best-effort basis by the open-source maintainers and community contributors. We aim to review incoming issues promptly, but response times may vary depending on maintainer availability and issue severity.

## Reporting Bugs and Seeking Help

When reporting an issue on GitHub, please include:

1. A clear description of the problem or unexpected behavior.
2. The version of the package or host client you are using.
3. Relevant error messages (ensuring no API keys, secrets, or confidential Odoo URLs are included).
4. Steps to reproduce the issue.

## Is the Server Up?

`/health` answers `{"status": "ok"}` and the running version, from the server itself, without signing in. If it answers and your assistant still cannot connect, the problem is in the connection rather than in the service, and the question below is the place to start.

## Frequently Asked Questions

### How do I disconnect my Odoo connection?
You can disconnect the integration directly inside your AI host application (e.g., Claude or ChatGPT). Disconnecting immediately revokes your active token family and purges your stored Odoo credentials from our remote database.

### What should I do if authorization fails?
Ensure that your Odoo base URL is reachable over HTTPS and that your API key belongs to a user with the permissions the work needs. If your instance is hosted on Odoo Online, the database name is required during consent because discovery cannot reach it.

## Contact

For direct inquiries or support concerns, please contact {{SUPPORT_EMAIL}}.

