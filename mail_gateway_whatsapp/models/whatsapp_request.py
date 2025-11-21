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
        requests = self.env['whatsapp.request'].search([('response', 'ilike', 'error'), ('write_date', '<=', today)])
        requests.write({
            'response': None
        })

    def cron_check_response_null(self):
        today = fields.date.today()
        requests = self.env['whatsapp.request'].search_count([('response', '=', False), ('write_date', '<=', today)])

        if requests >= 20:
            it_channel = self.env['mail.channel'].search([('name', 'ilike', 'TI / TI')], limit=1)

            it_channel.message_post(
                body=f"Atenção! Há requisições do WhatsApp sem resposta. ({requests})",
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
            )
