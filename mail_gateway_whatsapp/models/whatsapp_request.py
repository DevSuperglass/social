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
        requests = self.env['whatsapp.request'].search([('response', 'ilike', 'error'), ('write_date', '>=', today)])
        requests.write({
            'response': None
        })

    def cron_check_response_null(self):
        today = fields.date.today()
        requests = self.env['whatsapp.request'].search_count([('response', '=', False), ('write_date', '>=', today)])

        error_messages_ids = self.env['whatsapp.request'].search([
            ('response', 'ilike', 'error'),
            ('write_date', '>=', today)
        ]).mapped('id')

        if len(error_messages_ids) < 20 and requests < 20:
            return

        it_channel = self.env['mail.channel'].search([('name', 'ilike', 'TI / TI')], limit=1)

        odoobot_id = self.env['res.partner'].with_context(active_test=False).search(
            [
                ('name', '=', 'OdooBot'),
                ('user_id', '=', False)
            ],
            limit=1
        ).id

        if requests >= 20:
            last_message = self.env['mail.message'].search(
                [
                    ('res_id', '=', it_channel.id),
                    ('author_id', '=', odoobot_id),
                    ('date', '>=', today),
                    ('body', 'ilike', "[WhatsApp]")
                ],
                order='date desc',
                limit=1
            )

            body = f"<p><b>[WhatsApp]</b> Atenção! {requests} mensagens do whatsapp estão sem resposta, verifique o serviço <b>whatsapp_post</b></p>"

            if last_message.body != body:
                it_channel.message_post(
                    body=body,
                    message_type="comment",
                    subtype_xmlid="mail.mt_comment",
                    author_id=odoobot_id
                )

        if len(error_messages_ids) >= 20:
            last_message = self.env['mail.message'].search(
                [
                    ('res_id', '=', it_channel.id),
                    ('author_id', '=', odoobot_id),
                    ('date', '>=', today),
                    ('body', 'ilike', "[Mensagens com erro]")
                ],
                order='date desc',
                limit=1
            )

            body = f"<p><b>[Mensagens com erro]</b> ids({error_messages_ids})</p>"

            if last_message.body != body:
                it_channel.message_post(
                    body=body,
                    message_type="comment",
                    subtype_xmlid="mail.mt_comment",
                    author_id=odoobot_id
                )
