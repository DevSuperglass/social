from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    mail_gateway_whatsapp_groq_api_key = fields.Char(
        string='Groq API Key',
        config_parameter='mail_gateway_whatsapp.groq_api_key',
    )
