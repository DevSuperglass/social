# Copyright 2024 Dixmit
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import logging
import json
from odoo.http import Controller, request, route
from datetime import date

_logger = logging.getLogger(__name__)


class GatewayController(Controller):
    @route(
        "/gateway/<string:usage>/<string:token>/update",
        type="http",
        auth="public",
        methods=["GET", "POST"],
        csrf=False,
    )
    def post_update(self, usage, token, *args, **kwargs):
        if request.httprequest.method == "GET":
            bot_data = request.env["mail.gateway"]._get_gateway(
                token, gateway_type=usage, state="pending"
            )
            if not bot_data:
                return request.make_response(
                    json.dumps({}),
                    [
                        ("Content-Type", "application/json"),
                    ],
                )
            return (
                request.env["mail.gateway.%s" % usage]
                .with_user(bot_data["webhook_user_id"])
                ._receive_get_update(bot_data, request, **kwargs)
            )
        jsonrequest = json.loads(
            request.httprequest.get_data().decode(request.httprequest.charset)
        )

        # Auxiliar variables
        value = jsonrequest.get('entry', [])[0].get('changes', [])[0].get('value', {})
        messages = value.get('messages', [])
        statuses = value.get('statuses', [])

        if not messages and statuses:
            _logger.debug("Received a status update, not processing further.")
            return

        if request.env['mail.message'].sudo().search([('whatsapp_id', '=', messages[0].get('id'))]):
            return

        bot_data = request.env["mail.gateway"]._get_gateway(
            token, gateway_type=usage, state="integrated"
        )

        if not bot_data:
            _logger.warning(
                "Gateway was not found for token %s with usage %s", token, usage
            )
            return request.make_response(
                json.dumps({}),
                [
                    ("Content-Type", "application/json"),
                ],
            )

        jsonrequest = json.loads(
            request.httprequest.get_data().decode(request.httprequest.charset)
        )

        dispatcher = (
            request.env["mail.gateway.%s" % usage]
            .with_user(bot_data["webhook_user_id"])
            .with_context(no_gateway_notification=True)
        )

        if not dispatcher._verify_update(bot_data, jsonrequest):
            _logger.warning(
                "Message could not be verified for token %s with usage %s", token, usage
            )
            return request.make_response(
                json.dumps({}),
                [
                    ("Content-Type", "application/json"),
                ],
            )
        _logger.debug(
            "Received message for token %s with usage %s: %s",
            token,
            usage,
            json.dumps(jsonrequest),
        )

        gateway = dispatcher.env["mail.gateway"].browse(bot_data["id"])
        dispatcher._receive_update(gateway, jsonrequest)

        return request.make_response(
            json.dumps({}),
            [
                ("Content-Type", "application/json"),
            ],
        )
