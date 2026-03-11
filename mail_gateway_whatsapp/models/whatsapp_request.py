from odoo import fields, models


class WhatsappRequest(models.Model):
    _name = 'whatsapp.request'
    _description = 'Requisição WhatsApp'
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

        it_channel = self.env['mail.channel'].search([('name', 'ilike', 'TI / TI')], limit=1)

        odoobot_id = self.env['res.partner'].with_context(active_test=False).search(
            [
                ('name', '=', 'OdooBot'),
                ('user_id', '=', False)
            ],
            limit=1
        ).id

        messages = [{
            'body': (f"<p><b>[WhatsApp]</b> Atenção! {requests} mensagens do whatsapp estão sem resposta, "
                     f"verifique o serviço <b>whatsapp_post</b></p>"),
            'quantity': requests,
            'min_quantity': 20
        }]

        last_error_mail_message = self.env['mail.message'].search(
            [
                ('body', 'ilike', '[Mensagens com erro]'),
                ('author_id', '=', odoobot_id),
                ('res_id', '=', it_channel.id)
            ],
            order="write_date desc",
            limit=1
        )

        body = f"<p><b>[Mensagens com erro]</b> ids({error_messages_ids})</p>"
        if last_error_mail_message.body != body:
            messages.append({
                'body': body,
                'quantity': len(error_messages_ids),
                'min_quantity': 20
            })

        self._create_warning_messages(it_channel, odoobot_id, messages)

    def _create_warning_messages(self, channel, user_id, messages):
        for message in messages:
            if message['quantity'] >= message['min_quantity']:
                channel.message_post(
                    body=message['body'],
                    message_type="comment",
                    subtype_xmlid="mail.mt_comment",
                    author_id=user_id
                )
