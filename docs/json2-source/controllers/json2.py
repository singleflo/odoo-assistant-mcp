# Part of Odoo. See LICENSE file for full copyright and licensing details.

import inspect
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from werkzeug.exceptions import (
    Forbidden,
    NotFound,
    UnprocessableEntity,
)

from odoo import http  # type: ignore
from odoo.http import request
from odoo.models import BaseModel
from odoo.service.model import get_public_method
from odoo.tools import frozendict

_logger = logging.getLogger(__name__)


class WebJson2Controller(http.Controller):
    # Take over /json/<path:subpath>
    @http.route(
        ["/json/2", "/json/2/<path:subpath>"],
        auth="public",
        type="json2",
        readonly=True,
        methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    )
    def web_json_2_404(self, subpath=None):
        e = "Did you mean POST /json/2/<model>/<method>?"
        raise request.not_found(e)

    @http.route(
        "/json/2/<__model__>/<__method__>",
        methods=["POST"],
        auth="bearer",
        type="json2",
        save_session=False,
    )
    def web_json_2_rpc(
        self,
        __model__: str,
        __method__: str,
        ids: Sequence[int] = (),
        args: Sequence[Any] = (),
        context: Mapping[str, Any] = frozendict(),
        **kwargs,
    ):
        return self._dispatch_json2(
            __model__,
            __method__,
            ids,
            args,
            context,
            readonly_only=False,
            **kwargs,
        )

    @http.route(
        "/json/2/read/<__model__>/<__method__>",
        methods=["POST"],
        auth="bearer",
        type="json2",
        readonly=True,
        save_session=False,
    )
    def web_json_2_read_rpc(
        self,
        __model__: str,
        __method__: str,
        ids: Sequence[int] = (),
        args: Sequence[Any] = (),
        context: Mapping[str, Any] = frozendict(),
        **kwargs,
    ):
        """
        Read-only variant of /json/2/<model>/<method>.

        Accepts ONLY methods decorated with @api.readonly.
        Enforced at application level (the route's readonly=True flag is a
        performance hint, not a security barrier, because Odoo's framework
        retries with a read/write cursor on ReadOnlySqlTransaction).

        :raises Forbidden: if the target method is not @api.readonly.
        """
        return self._dispatch_json2(
            __model__,
            __method__,
            ids,
            args,
            context,
            readonly_only=True,
            **kwargs,
        )

    def _dispatch_json2(
        self,
        model_name: str,
        method_name: str,
        ids: Sequence[int],
        args: Sequence[Any],
        context: Mapping[str, Any],
        readonly_only: bool,
        **kwargs,
    ):
        try:
            Model = request.env[model_name].with_context(context)
        except KeyError as exc:
            e = f"the model {model_name!r} does not exist"
            raise NotFound(e) from exc

        try:
            func = get_public_method(Model, method_name)
        except AttributeError as exc:
            raise NotFound(exc.args[0]) from exc

        if readonly_only and not getattr(func, "_readonly", False):
            e = (
                f"{model_name}.{method_name} is not @api.readonly. "
                f"Use /json/2/{model_name}/{method_name} for non-readonly methods."
            )
            raise Forbidden(e)

        if hasattr(func, "_api_model") and ids:
            e = f"cannot call {model_name}.{method_name} with ids"
            raise UnprocessableEntity(e)

        records = Model.browse(ids)
        signature = inspect.signature(func)
        try:
            signature.bind(records, *args, **kwargs)
        except TypeError as exc:
            raise UnprocessableEntity(exc.args[0])

        result = func(records, *args, **kwargs)
        if isinstance(result, BaseModel):
            result = result.ids

        return result
