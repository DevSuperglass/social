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
        current_date = date.today()
        entry = jsonrequest.get('entry', [])
        changes = entry[0].get('changes', [])
        value = changes[0].get('value', {})
        messages = value.get('messages', [])
        statuses = value.get('statuses', [])

        if not messages and statuses:
            _logger.debug("Received a status update, not processing further.")
            return

        partner_name = jsonrequest['entry'][0]['changes'][0]['value']['contacts'][0]['profile']['name']
        button_template = messages[0].get('button', {}).get('payload', False)
        partner = request.env['res.partner']
        context_id = None
        reply_id = None
        from_webhook = True

        whats_id = messages[0].get('id')
        numero = value.get('contacts', [])[0].get('wa_id')

        if request.env['mail.message'].sudo().search([('whatsapp_id', '=', whats_id)]):
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

        if not request.env['res.partner'].sudo().search([('phone_sanitized', '=', "+" + numero)]):
            vals_list = {
                'name': partner_name,
            }

            vals_list.update({'phone': numero, 'whatsapp_contact': 'phone'}) if len(numero) == 12 else vals_list.update(
                {'mobile': numero, 'whatsapp_contact': 'mobile'})
            partner = request.env['res.partner'].sudo().create(vals_list)

        for message in messages:
            context = message.get('context', {})
            if context:
                context_id = context.get('id')
                reply_id = request.env['mail.message'].sudo().search([('whatsapp_id', '=like', context_id)], limit=1).id

        # if messages[0]['type'] == 'text':
        #     message = messages[0]['text']['body']
        # else:
        #     message = button_template

        # LOGS
        # _logger.info("ANTES DO RECEIVE UPDATE")
        # _logger.info("CONTATO EMISSOR: {}".format(partner_name))
        # _logger.info("MENSAGEM QUE CHEGOU: {}".format(message))
        # _logger.info("WHATS_ID DA MENSAGEM: {}".format(whats_id))
        # _logger.info("PARENT_ID DA MENSAGEM: {}".format(context_id))
        # _logger.info("MENSAGEM PAI DENTRO DO ODOO: {}".format(reply_id))

        gateway = dispatcher.env["mail.gateway"].browse(bot_data["id"])
        dispatcher._receive_update(gateway, jsonrequest, whats_id, reply_id, from_webhook)

        # LOGS
        # _logger.info("--------------------------")
        # _logger.info("APÓS O RECEIVE UPDATE")
        # _logger.info("WHATS_ID DA MENSAGEM: {}".format(whats_id))
        # _logger.info("PARENT_ID DA MENSAGEM: {}".format(context_id))
        # _logger.info("MENSAGEM PAI DENTRO DO ODOO: {}".format(reply_id))

        change_status = request.env['crm.lead'].sudo().search(
            [('mobile', '=', numero), ('new_status', '=', 'draft')])

        if change_status:
            change_status.new_status = 'in_progress'
            change_status.remove_button = True

        if button_template:
            button_record = request.env['whatsapp.template.button'].sudo().search(
                [('name', '=', button_template), ('whatsapp_template_id', '=',
                                                  request.env['whatsapp.template'].sudo().search(
                                                      [('wa_ids.wa_id', '=like', context_id)]).id)])
            if button_record.code:
                model = button_record.env[button_record.model_id.model].with_context(
                    numero_formatado=numero,
                    button=button_template,
                    partner_name=partner_name,
                    partner=partner,
                    current_date=current_date,
                    json=entry,
                    waid=context_id
                )
                function_to_call = getattr(model, button_record.code, None)
                if callable(function_to_call):
                    function_to_call()
                else:
                    return False
            else:
                _logger.warning("Button template not found")

        # LOGS
        # _logger.info("--------------------------")
        # _logger.info("APÓS O RECEIVE UPDATE E CHAMADA DE BOTÃO")
        # _logger.info("WHATS_ID DA MENSAGEM: {}".format(whats_id))
        # _logger.info("PARENT_ID DA MENSAGEM: {}".format(context_id))
        # _logger.info("MENSAGEM PAI DENTRO DO ODOO: {}".format(reply_id))

        return request.make_response(
            json.dumps({}),
            [
                ("Content-Type", "application/json"),
            ],
        )
