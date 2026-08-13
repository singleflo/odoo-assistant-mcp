{
    "name": "API Documentation",
    "category": "Hidden",
    "version": "18.0.2.0.0",
    "description": """
Odoo Dynamic API Documentation
==============================

This module provides a dynamic documentation page for developpers at the
/doc URL. The documentation is generated using the database to list the
models and their fields and methods. It also provides a playground to run
the methods over HTTP, with examples in various programming languages.

Endpoints
---------

- ``POST /json/2/<model>/<method>`` (bearer auth): full RPC. Accepts any
  public method (read and write). Used by admin tools and scripts that
  need to create/update/delete records.
- ``POST /json/2/read/<model>/<method>`` (bearer auth, readonly): accepts
  ONLY methods decorated with ``@api.readonly``. Returns 403 Forbidden
  otherwise. Safe to expose to tooling that must not mutate data
  (BA dashboards, read-only integrations, LLM query assistants).
  Note: enforcement is application-level (method decorator check). The
  route's ``readonly=True`` flag is a performance hint, not a security
  barrier, because Odoo retries with a read/write cursor on
  ``ReadOnlySqlTransaction``.
- ``GET /doc-bearer/index.json`` and ``/doc-bearer/<model>.json``
  (bearer auth): read-only schema introspection.
""",
    "depends": ["web"],
    "auto_install": False,
    "data": [
        "security/res_groups.xml",
        "views/docclient.xml",
    ],
    "assets": {
        "api_doc.assets": [
            # Libs
            "web/static/src/libs/fontawesome/css/font-awesome.css",
            "web/static/src/scss/fontawesome_overridden.scss",
            # Core
            "web/static/src/module_loader.js",
            "web/static/lib/owl/owl.js",
            "web/static/lib/owl/odoo_module.js",
            # Utils
            "web/static/src/core/utils/functions.js",
            "web/static/src/core/utils/reactive.js",
            "web/static/src/core/browser/browser.js",
            "web/static/src/core/utils/timing.js",
            "web/static/src/core/template_inheritance.js",
            "web/static/src/core/templates.js",
            "web/static/src/core/registry.js",
            "web/static/src/session.js",
            "web/static/src/core/assets.js",
            "web/static/src/core/code_editor/**",
            # Bootstrap
            ("include", "web._assets_helpers"),
            "web/static/src/scss/pre_variables.scss",
            "web/static/lib/bootstrap/scss/_variables.scss",
            "web/static/lib/bootstrap/scss/_variables-dark.scss",
            "web/static/lib/bootstrap/scss/_maps.scss",
            ("include", "web._assets_bootstrap"),
            # Static files
            "api_doc/static/src/**/*.xml",
            "api_doc/static/src/**/*.js",
            "api_doc/static/src/doc_client.css",
            ("remove", "api_doc/static/src/api_action.js"),
        ],
        "web.assets_backend": [
            "api_doc/static/src/api_action.js",
        ],
    },
    "bootstrap": True,
    "installable": True,
    "application": False,
    "author": "Odoo S.A.",
    "license": "LGPL-3",
}
