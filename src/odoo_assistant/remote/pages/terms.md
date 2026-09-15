# Terms of Service

Last updated: 16 September 2026.

These Terms of Service govern your access to and use of the hosted remote server provided by {{PUBLISHER}}. By authorizing an Odoo instance connection through this service, you agree to these terms.

## Who Provides It

The service is operated by {{PUBLISHER}}, reachable at {{SUPPORT_EMAIL}}. It is an independent project: Odoo is a trademark of Odoo S.A., and nothing here is affiliated with, endorsed or sponsored by Odoo S.A.

## Open Source Foundation

The odoo-assistant project and server software are open-source software licensed under the MIT License. You are free to inspect, host, modify, or run your own instance of the server in accordance with the terms of the MIT License.

## Provided As-Is

The hosted service is provided on an "AS IS" and "AS AVAILABLE" basis, without warranties of any kind, whether express, implied, statutory, or otherwise. 

To the maximum extent permitted by law, {{PUBLISHER}} disclaims all warranties, including but not limited to implied warranties of merchantability, fitness for a particular purpose, non-infringement, and continuous uninterrupted availability. We do not guarantee that the service will meet your specific operational requirements or operate error-free.

## User Responsibility for Credentials and Access

You are solely responsible for:

* The accuracy, validity, and permissions of the Odoo instance URL and API credentials you provide during consent.
* Ensuring that the API user account connected to this service possesses appropriate privileges within your Odoo database.
* Compliance with your organization's security policies and data governance regulations regarding third-party integrations.
* Managing access revocation when connection to an Odoo instance is no longer required.

## Operation Scope and Safeguards

Our hosted remote server acts as an operational bridge to execute authorized Odoo actions requested by your client host. 

Please note that our hosted bridge is structured to respect your configured safety settings. Database record deletion capabilities (`unlink` operations) are never available through the hosted route — no administrative flag can change this, as the safety gate refuses deletion for every connected tenant. The only deletion path in this project is a local install running on your own machine, where you as the operator have deliberately set `ODOO_MCP_ALLOW_UNLINK=yes`. You remain responsible for reviewing and controlling actions issued by connected AI assistants.

## Acceptable Use

Connect only an Odoo instance you are entitled to connect, with a key whose permissions you are entitled to grant. Do not use the service to reach an instance that is not yours, to work around the access rights of the Odoo user the key belongs to, to send bulk or unsolicited messages, or for anything unlawful where you are. We may refuse or end a connection that does any of these.

## Suspension, Changes and Termination

The hosted service may be suspended, changed or discontinued, with notice where that is practical and without it where a security or abuse problem makes waiting worse. You may end your use at any time by disconnecting the integration, which revokes your tokens and erases the stored connection. These terms may change; the date at the top says when they last did, and continuing to use the service after a change accepts it. The software itself stays MIT-licensed and self-hostable, so no decision taken here can take the capability away from you.

## Governing Law

These terms are governed by the law of the place where {{PUBLISHER}} is established, and disputes belong to the courts competent there, without prejudice to the protections that mandatory consumer law may give you where you live.

## Limitation of Liability

In no event shall {{PUBLISHER}} or the project maintainers be liable for any direct, indirect, incidental, special, consequential, or exemplary damages, including but not limited to loss of data, loss of business revenue, or operational interruption arising out of the use or inability to use this service.

## Contact and Support

If you have questions regarding these Terms of Service or need technical assistance, please reach out to {{SUPPORT_EMAIL}} (defaults to https://github.com/singleflo/odoo-assistant-mcp/issues).
