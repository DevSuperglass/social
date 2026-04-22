from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    mail_gateway_whatsapp_transcription_api_key = fields.Char(
        string='API Key de Transcrição (Groq · whisper-large-v3)',
        config_parameter='mail_gateway_whatsapp.transcription_api_key',
    )
