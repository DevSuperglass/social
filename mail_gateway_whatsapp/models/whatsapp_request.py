from odoo import fields, models


class WhatsappRequest(models.Model):
    _name = 'whatsapp.request'

    url = fields.Char()

    headers = fields.Json()

    json = fields.Json()

    response = fields.Char()

    mail_message_id = fields.Many2one(
        comodel_name="mail.message"
    )


