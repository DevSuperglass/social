from odoo import fields, models, api


class MailMessage(models.Model):
    _inherit = 'mail.message'
    _description = "Extensão do modelo mail.message"

    whatsapp_id = fields.Char()
    whatsapp_decoded_id = fields.Char()
