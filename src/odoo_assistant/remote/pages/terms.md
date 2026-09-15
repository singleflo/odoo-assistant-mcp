# Terms of Service for odoo-assistant Hosted Remote Server

These Terms of Service govern your access to and use of the hosted remote server provided by {{PUBLISHER}}. By authorizing an Odoo instance connection through this service, you agree to these terms.

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

Please note that our hosted bridge is structured to respect your configured safety settings. By design, database record deletion capabilities (`unlink` operations) are disabled through the hosted route unless explicit server-side administrative flags permit them. You remain responsible for reviewing and controlling actions issued by connected AI assistants.

## Limitation of Liability

In no event shall {{PUBLISHER}} or the project maintainers be liable for any direct, indirect, incidental, special, consequential, or exemplary damages, including but not limited to loss of data, loss of business revenue, or operational interruption arising out of the use or inability to use this service.

## Contact and Support

If you have questions regarding these Terms of Service or need technical assistance, please reach out to {{SUPPORT_EMAIL}} (defaults to https://github.com/singleflo/odoo-assistant-mcp/issues).
