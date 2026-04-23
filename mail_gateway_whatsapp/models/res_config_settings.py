from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    mail_gateway_whatsapp_transcription_provider = fields.Selection(
        selection=[
            ('groq', 'Groq (whisper-large-v3)'),
            ('deepgram', 'Deepgram (nova-2, pt-BR)'),
            ('assemblyai', 'AssemblyAI (Universal, pt)'),
        ],
        string='Provedor de Transcrição',
        config_parameter='mail_gateway_whatsapp.transcription_provider',
    )

    mail_gateway_whatsapp_groq_api_key = fields.Char(
        string='API Key — Groq',
        config_parameter='mail_gateway_whatsapp.groq_api_key',
    )

    mail_gateway_whatsapp_deepgram_api_key = fields.Char(
        string='API Key — Deepgram',
        config_parameter='mail_gateway_whatsapp.deepgram_api_key',
    )

    mail_gateway_whatsapp_assemblyai_api_key = fields.Char(
        string='API Key — AssemblyAI',
        config_parameter='mail_gateway_whatsapp.assemblyai_api_key',
    )
