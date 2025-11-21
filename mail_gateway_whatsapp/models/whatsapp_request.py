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
        pass

    def cron_check_response_null(self):
        requests = self.env['whatsapp.request'].search_count([('response', '=', False)])

        if requests >= 10:
            it_channel = self.env['mail.channel'].search([('name', 'ilike', 'TI / TI')], limit=1)

            it_channel.message_post(
                body=f"Atenção! Há requisições do WhatsApp sem resposta. ({requests})",
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
            )
