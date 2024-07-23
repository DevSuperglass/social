# Copyright 2024 Dixmit
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import logging
import json
import random
from odoo.http import Controller, request, route
from datetime import datetime, date
import datetime
import requests.exceptions

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
        current_date = date.today()
        whats_id = request.httprequest.json['entry'][0]['changes'][0]['value']['messages'][0]['id']
        entry = jsonrequest.get('entry', [])
        _logger.info(entry)
        if not entry:
            _logger.error("No entry found in jsonrequest")
            return

        changes = entry[0].get('changes', [])
        if not changes:
            _logger.error("No changes found in entry")
            return

        value = changes[0].get('value', {})
        if not value:
            _logger.error("No value found in changes")
            return

        messages = value.get('messages', [])
        statuses = value.get('statuses', [])

        if not messages and statuses:
            _logger.debug("Received a status update, not processing further.")
            return

        if not messages:
            _logger.error("No messages found in value")
            return

        contacts = value.get('contacts', [])
        if not contacts:
            _logger.error("No contacts found in value")
            return

        numero = contacts[0].get('wa_id')
        if not numero:
            _logger.error("No wa_id found in contacts")
            return

        numero_formatado = "+{} {} {}-{}".format(numero[:2], numero[2:4], numero[4:9], numero[9:])
        partner_name = jsonrequest['entry'][0]['changes'][0]['value']['contacts'][0]['profile']['name']

        button_template = messages[0].get('button', {}).get('payload', False)

        partner = request.env['res.partner'].sudo().search([('mobile', '=', numero_formatado)])

        if not partner:
            department_id = request.env['hr.department'].sudo().search(
                [('complete_name', '=', 'SUPERGLASS / VENDAS')]).id
            parent_id = request.env['category_request'].sudo().search([('name', '=', 'VENDAS')]).id
            child_id = request.env['category_request'].sudo().search([('name', '=like', 'COTAÇÃO SOLICITADA')]).id

            vals_list = {
                'name': partner_name,
                'mobile': numero_formatado,
            }
            request.env['res.partner'].sudo().create(vals_list)
            partner = request.env['res.partner'].sudo().search([('mobile', '=', numero_formatado)])

            new_partner_vals = {
                'status': 'aberto',
                'private_message': 'public',
                'department_id': department_id,
                'user_requested_id': False,
                'category_parent_request_id': parent_id,
                'category_child_request': child_id,
                'boolean_client': True,
                'description_problem': f'Novo contato {partner_name} com celular {numero_formatado} criado, entrar em contato para o completar o cadastro.',
                'message_follower_ids': [],
                'activity_ids': [],
                'message_ids': [],
                'request_client_ids': partner.ids,
                'update_date': current_date,
                'opening_date': current_date
            }
            gerproc_create = request.env["project_request"].sudo().create(new_partner_vals)

        if button_template:
            button_record = request.env['whatsapp.template.button'].sudo().search([('name', '=', button_template)],
                                                                                  order='create_date desc', limit=1)
            function_name = button_record.code
            if function_name:
                button_record.with_context(
                    numero_formatado=numero_formatado,
                    partner_name=partner_name,
                    partner=partner,
                    function_name=function_name,
                    current_date=current_date
                ).call_function()
            else:
                print("Button template not found")

        # change_status = request.env['crm.lead'].sudo().search(
            # [('phone', '=', numero_formatado), ('new_status', '=', 'draft'), ('remove_button', '=', False)])
        # if change_status.phone == numero_formatado:
            # change_status.new_status = 'in_progress'
            # change_status.remove_button = True

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
        dispatcher._receive_update(gateway, jsonrequest, whats_id)

        return request.make_response(
            json.dumps({}),
            [
                ("Content-Type", "application/json"),
            ],
        )
