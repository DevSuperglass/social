from odoo import fields, models


class WhatsappRequest(models.Model):
    _name = 'whatsapp.request'

    url = fields.Char()

    headers = fields.Json()

    json = fields.Json()

    response = fields.Char(
        index=True
    )

    mail_message_id = fields.Many2one(
        comodel_name="mail.message"
    )

    def action_button_reset_response(self):
        today = fields.date.today()
        non_response_requests = self.env['whatsapp.request'].search(
            [('response', 'ilike', 'error'), ('write_date', '>=', today)])
        non_response_requests.write({
            'response': None
        })

    def cron_check_response_null(self):
        today = fields.date.today()
        requests = self.env['whatsapp.request'].search_count([('response', '=', False), ('write_date', '>=', today)])

        error_messages_ids = self.env['whatsapp.request'].search([
            ('response', 'ilike', 'error'),
            ('write_date', '>=', today)
        ]).mapped('id')
        count_error_messages = len(error_messages_ids)

        it_channel = self.env['mail.channel'].search([('name', 'ilike', 'TI / TI')], limit=1)

        odoobot_id = self.env['res.partner'].with_context(active_test=False).search(
            [
                ('name', '=', 'OdooBot'),
                ('user_id', '=', False)
            ],
            limit=1
        ).id

        body = (f"<p><b>[WhatsApp]</b> Atenção! {requests} mensagens do whatsapp estão sem resposta, "
                f"verifique o serviço <b>whatsapp_post</b></p>")
        self._create_warning_message(it_channel, odoobot_id, body, requests)

        body = f"<p><b>[Mensagens com erro]</b> ids({error_messages_ids})</p>"
        self._create_warning_message(it_channel, odoobot_id, body, count_error_messages)

    def _create_warning_message(self, channel, user_id, body, count_messages, min_messages=20):
        if count_messages >= min_messages:
            channel.message_post(
                body=body,
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
                author_id=user_id
            )
