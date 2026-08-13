# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""
Json2Dispatcher - Modern JSON API dispatcher for Odoo 18

This module provides a Json2Dispatcher class that handles JSONRPC requests
with support for bearer token authentication and empty request bodies.

The dispatcher auto-registers itself when the module is loaded, making
type='json2' available for route decorators.
"""

import collections.abc
from http import HTTPStatus

import werkzeug.exceptions

from odoo.exceptions import UserError  # type: ignore
from odoo.http import Dispatcher, Response, serialize_exception  # type: ignore


# Monkey-patch SessionExpiredException to support http_status attribute
# This allows Json2Dispatcher to return proper HTTP 403 for expired sessions
def _patch_session_expired_exception():
    """Add http_status attribute to SessionExpiredException if not present."""
    from odoo.http import SessionExpiredException  # type: ignore

    if not hasattr(SessionExpiredException, "http_status"):
        SessionExpiredException.http_status = HTTPStatus.FORBIDDEN


_patch_session_expired_exception()


class Json2Dispatcher(Dispatcher):
    """
    JSON dispatcher that handles both empty bodies and JSON payloads.

    This is a simplified version of JsonRPCDispatcher designed for modern
    JSON APIs with bearer token authentication support.

    Features:
    - Supports empty request bodies (GET-style JSON endpoints)
    - Supports JSON request bodies (POST-style JSON endpoints)
    - Auto-converts dict/list returns to JSON responses
    - Proper error handling with JSON error responses
    - Bearer token authentication compatible
    """

    routing_type = "json2"
    mimetypes = ("application/json",)

    def __init__(self, request):
        super().__init__(request)
        self.jsonrequest = None

    @classmethod
    def is_compatible_with(cls, request):
        """Check if request is compatible with json2 dispatcher."""
        return (
            request.httprequest.mimetype in cls.mimetypes
            or not request.httprequest.content_length
        )

    def dispatch(self, endpoint, args):
        """
        Dispatch the request to the endpoint.

        Args:
            endpoint: The controller method to call
            args: Path parameters from the route (e.g., model_name, method_name)

        Returns:
            Response object with JSON content
        """
        # Parse JSON body if present
        if self.request.httprequest.content_length:
            try:
                self.jsonrequest = self.request.get_json_data()
            except ValueError as exc:
                e = f"could not parse the body as json: {exc.args[0]}"
                raise werkzeug.exceptions.BadRequest(e) from exc

        # Merge JSON params with path params
        try:
            self.request.params = self.jsonrequest | args
        except TypeError:
            self.request.params = dict(args)  # make a copy

        # Call endpoint
        if self.request.db:
            result = self.request.registry["ir.http"]._dispatch(endpoint)
        else:
            result = endpoint(**self.request.params)

        # Return response (auto-convert dict/list to JSON)
        if isinstance(result, Response):
            return result
        return self.request.make_json_response(result)

    def handle_error(self, exc: Exception) -> collections.abc.Callable:
        """
        Handle exceptions and return appropriate JSON error responses.

        Args:
            exc: The exception to handle

        Returns:
            Response object with JSON error content
        """
        # If exception already has a response, use it
        if isinstance(exc, werkzeug.exceptions.HTTPException) and exc.response:
            return exc.response  # type: ignore

        # Determine HTTP status code
        headers = None
        if isinstance(exc, (UserError,)):
            # Check for http_status attribute (SessionExpiredException has it)
            status = getattr(exc, "http_status", HTTPStatus.BAD_REQUEST)  # type: ignore
            body = serialize_exception(exc)
        elif isinstance(exc, werkzeug.exceptions.HTTPException):
            status = exc.code
            body = serialize_exception(exc)
            # Strip Content-Type but keep remaining headers
            try:
                ct, *headers = exc.get_headers()
                assert ct == ("Content-Type", "text/html; charset=utf-8")
            except (AttributeError, AssertionError, ValueError):
                headers = None
        else:
            status = 500  # HTTPStatus.INTERNAL_SERVER_ERROR
            body = serialize_exception(exc)

        return self.request.make_json_response(body, headers=headers, status=status)
