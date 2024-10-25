from odoo import fields, models, api


class MailMessage(models.Model):
    _inherit = 'mail.message'

    whatsapp_id = fields.Char()
    from_webhook = fields.Boolean(default=False)
