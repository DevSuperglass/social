from odoo import fields, models, api


class MailMessage(models.Model):
    _inherit = 'mail.message'
    _description = "Extensão de Mail Message (Social)"

    whatsapp_id = fields.Char()
    whatsapp_decoded_id = fields.Char()
