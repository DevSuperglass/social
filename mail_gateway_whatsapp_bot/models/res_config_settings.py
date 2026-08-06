from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ai_agent_url = fields.Char(
        string='URL do Agente IA',
        config_parameter='cotacoes.ai_agent_url',
        default='http://localhost:8000',
    )
    ai_agent_secret = fields.Char(
        string='Secret da IA',
        config_parameter='cotacoes.ai_agent_secret',
    )
    bot_idle_timeout_minutes = fields.Integer(
        string='Timeout Ocioso Bot (min)',
        config_parameter='cotacoes.bot_idle_timeout_minutes',
        default=5,
    )
