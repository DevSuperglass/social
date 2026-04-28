from odoo import fields, models, api


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    ai_agent_url = fields.Char(
        string="URL do Agente IA",
        store=True
    )

    ai_agent_secret = fields.Char(
        string="Secret do Agente IA",
        store=True
    )

    bot_idle_timeout_minutes = fields.Integer(
        string="Timeout de Ociosidade do Bot (minutos)",
        store=True
    )

    @api.model
    def get_values(self):
        res = super().get_values()
        config = self.env['ir.config_parameter'].sudo()
        res.update({
            'ai_agent_url': config.get_param('cotacoes.ai_agent_url', 'http://localhost:8080'),
            'ai_agent_secret': config.get_param('cotacoes.ai_agent_secret', ''),
            'bot_idle_timeout_minutes': int(config.get_param('cotacoes.bot_idle_timeout_minutes', '5')),
        })
        return res

    @api.model
    def set_values(self):
        super().set_values()
        config = self.env['ir.config_parameter'].sudo()
        config.set_param('cotacoes.ai_agent_url', self.ai_agent_url or 'http://localhost:8080')
        config.set_param('cotacoes.ai_agent_secret', self.ai_agent_secret or '')
        config.set_param('cotacoes.bot_idle_timeout_minutes', self.bot_idle_timeout_minutes or 5)
