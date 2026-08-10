from odoo import fields, models, api

from ..models.whatsapp_cache_notifier import _notify_cache_refresh


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

    human_escalation_attempts = fields.Integer(
        string="Tentativas até Escalonamento Humano",
        store=True
    )

    @api.model
    def get_values(self):
        res = super().get_values()
        config = self.env['ir.config_parameter'].sudo()
        res.update({
            'ai_agent_url': config.get_param('cotacoes.ai_agent_url', 'http://localhost:8000'),
            'ai_agent_secret': config.get_param('cotacoes.ai_agent_secret', ''),
            'bot_idle_timeout_minutes': int(config.get_param('cotacoes.bot_idle_timeout_minutes', '5')),
            'human_escalation_attempts': int(config.get_param('cotacoes.human_escalation_attempts', '50')),
        })
        return res

    @api.model
    def set_values(self):
        super().set_values()
        config = self.env['ir.config_parameter'].sudo()
        config.set_param('cotacoes.ai_agent_url', self.ai_agent_url or 'http://localhost:8000')
        config.set_param('cotacoes.ai_agent_secret', self.ai_agent_secret or '')
        idle_minutes = self.bot_idle_timeout_minutes or 5
        config.set_param('cotacoes.bot_idle_timeout_minutes', idle_minutes)
        cron = self.env.ref('mail_gateway_whatsapp_bot.cron_close_idle_bot_attendances', raise_if_not_found=False)
        if cron:
            cron.sudo().write({'interval_number': idle_minutes, 'interval_type': 'minutes'})

        escalation_attempts = self.human_escalation_attempts or 50
        config.set_param('cotacoes.human_escalation_attempts', escalation_attempts)
        _notify_cache_refresh(self.env, 'human_escalation_attempts')
