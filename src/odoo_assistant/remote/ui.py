"""One document shell for every human-facing page of the hosted server.

The landing page, the three policy pages and the consent form used to build
their own `<html>` string each, with no stylesheet and no viewport meta, so
the consent form — the page a user reaches from a phone, through the in-app
browser of Claude or ChatGPT, to type the API key of a production ERP —
rendered zoomed out in browser-default serif. They now share `layout()`.

Two rules this module exists to keep:

* **No external request.** The stylesheet is served by this same server from
  `pages/style.css`, the mark is an inline SVG data URI, and there is no
  script and no web font — a page that asks for a credential must not also
  ask a CDN to watch it being typed. That is also what lets the Content
  Security Policy in `app.py` be as narrow as `default-src 'none'`.
* **The stylesheet is cache-busted by version.** `/style.css?v=<version>`
  with a one-year immutable cache: a deploy changes the query, so nobody
  reads a new page through an old stylesheet.
"""
import html

from odoo_assistant import __version__

REPO_URL = "https://github.com/singleflo/odoo-assistant-mcp"

# The listing icon as 500 bytes of vector, inline: no route, no file, no
# second request, and it survives a CSP that allows `img-src 'self' data:`.
_MARK = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
    "%3Crect width='32' height='32' rx='7' fill='%231b1f2a'/%3E"
    "%3Ccircle cx='13' cy='16' r='5.2' fill='none' stroke='%23ffffff'"
    " stroke-width='2.4'/%3E"
    "%3Cpath d='M21 21 L24.6 11 L28.2 21 M22.2 18 L27.4 18'"
    " fill='none' stroke='%23ffffff' stroke-width='2.4'"
    " stroke-linecap='round' stroke-linejoin='round'/%3E"
    "%3C/svg%3E")

_NAV = (("/", "Overview"), ("/privacy", "Privacy"), ("/terms", "Terms"),
        ("/support", "Support"))


def layout(title: str, body: str, *, publisher: str,
           active: str | None = None, lead: str | None = None) -> str:
    """The full document for one page.

    `title` is both the `<title>` and the page's `<h1>`; `lead` is the one
    sentence under it. `active` is the nav path to mark as current, and is
    None on the consent page, which is reached from a client rather than
    from the navigation.
    """
    links = []
    for path, label in _NAV:
        current = ' aria-current="page"' if path == active else ""
        links.append(f'<a href="{path}"{current}>{html.escape(label)}</a>')
    nav = "".join(links)
    lead_html = f'<p class="lead">{lead}</p>' if lead else ""
    return (
        "<!doctype html>"
        "<html lang=\"en\">"
        "<head>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,"
        " initial-scale=1\">"
        "<meta name=\"color-scheme\" content=\"light dark\">"
        f"<title>{html.escape(title)} — Odoo Assistant</title>"
        f"<link rel=\"icon\" href=\"{_MARK}\">"
        f"<link rel=\"stylesheet\" href=\"/style.css?v={_version()}\">"
        "</head>"
        "<body>"
        "<header class=\"site-header\">"
        "<div class=\"header-inner\">"
        f"<a class=\"brand\" href=\"/\"><img src=\"{_MARK}\" alt=\"\""
        " width=\"28\" height=\"28\"><span>Odoo Assistant</span></a>"
        f"<nav class=\"site-nav\" aria-label=\"Pages\">{nav}</nav>"
        "</div>"
        "</header>"
        "<main class=\"page\">"
        f"<h1>{html.escape(title)}</h1>{lead_html}{body}"
        "</main>"
        f"{_footer(publisher)}"
        "</body></html>")


def _footer(publisher: str) -> str:
    """Publisher, links, and the trademark line.

    The trademark sentence is not decoration: this server integrates Odoo
    without any relationship to Odoo S.A., and a listing that lets a reader
    infer endorsement is the kind of claim a directory review rejects.
    """
    return (
        "<footer class=\"site-footer\">"
        "<div class=\"footer-inner\">"
        f"<p>Published by {html.escape(publisher)}. Open source under the MIT"
        f" licence — <a href=\"{REPO_URL}\">source and documentation</a>.</p>"
        "<p class=\"trademark\">Odoo is a trademark of Odoo S.A. This is an"
        " independent project: not affiliated with, endorsed or sponsored by"
        " Odoo S.A.</p>"
        "</div>"
        "</footer>")


def _version() -> str:
    return html.escape(__version__)
