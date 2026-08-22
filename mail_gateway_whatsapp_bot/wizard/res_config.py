from odoo import fields, models, api

from ..models.whatsapp_cache_notifier import _notify_cache_refresh

# Crons diários do bot cujo horário (Brasília) passa a ser configurável aqui,
# em vez de fixo no nextcall do XML — com valores separados pra dia útil
# (segunda a sexta) e sábado. Cálculo de nextcall e offset UTC-3 (Brasília)
# vivem em quotation._next_bot_cron_call/_reschedule_bot_cron, única fonte de
# verdade reusada tanto pelo self-reschedule ao final de cada cron quanto por
# este set_values (efeito imediato ao mudar o horário pela tela).
_SCHEDULED_CRONS = {
    'cron_hour_confirm': (
        'mail_gateway_whatsapp_bot.cron_confirm_bot_quotations', 16.0, 16.0
    ),
    'cron_hour_notify_loaded': (
        'mail_gateway_whatsapp_bot.cron_notify_bot_quotations_loaded', 16.25, 16.25
    ),
    'cron_hour_send_hitec': (
        'mail_gateway_whatsapp_bot.cron_send_bot_quotations_to_hitec', 16.5, 16.5
    ),
}


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

    cron_hour_confirm_weekday = fields.Float(
        string="Confirmação Automática de Cotações (Seg-Sex)",
        widget='float_time',
        store=True,
    )
    cron_hour_confirm_saturday = fields.Float(
        string="Confirmação Automática de Cotações (Sábado)",
        widget='float_time',
        store=True,
    )

    cron_hour_notify_loaded_weekday = fields.Float(
        string="Aviso de Pré-Carregamento (Seg-Sex)",
        widget='float_time',
        store=True,
    )
    cron_hour_notify_loaded_saturday = fields.Float(
        string="Aviso de Pré-Carregamento (Sábado)",
        widget='float_time',
        store=True,
    )

    cron_hour_send_hitec_weekday = fields.Float(
        string="Envio de Pedido ao Hitec (Seg-Sex)",
        widget='float_time',
        store=True,
    )
    cron_hour_send_hitec_saturday = fields.Float(
        string="Envio de Pedido ao Hitec (Sábado)",
        widget='float_time',
        store=True,
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
        for prefix, (xmlid, default_weekday, default_saturday) in _SCHEDULED_CRONS.items():
            res[f'{prefix}_weekday'] = float(config.get_param(f'cotacoes.{prefix}_weekday', default_weekday))
            res[f'{prefix}_saturday'] = float(config.get_param(f'cotacoes.{prefix}_saturday', default_saturday))
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

        # Sempre reagenda ao salvar (não só "se mudou"): comparar valor
        # anterior x novo é frágil demais aqui — se o nextcall já estivesse
        # desatualizado por qualquer outro motivo (ex: salvo antes do horário
        # de sábado existir), um save sem mudança real nunca o corrigiria.
        quotation = self.env['quotation']
        for prefix, (xmlid, default_weekday, default_saturday) in _SCHEDULED_CRONS.items():
            hour_weekday = getattr(self, f'{prefix}_weekday') or default_weekday
            hour_saturday = getattr(self, f'{prefix}_saturday') or default_saturday
            config.set_param(f'cotacoes.{prefix}_weekday', hour_weekday)
            config.set_param(f'cotacoes.{prefix}_saturday', hour_saturday)
            quotation._reschedule_bot_cron(xmlid, prefix, default_weekday, default_saturday)
