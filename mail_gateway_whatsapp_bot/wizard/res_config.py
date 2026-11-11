import logging

import requests

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

# Maior que os 10 s que o serviço espera pelo DeepSeek: o serviço responde (com erro, se for o caso) antes de o
# Odoo desistir, e a tela de definições nunca fica presa.
DEEPSEEK_BALANCE_TIMEOUT_SECONDS = 15

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

    def action_check_deepseek_balance(self):
        """Botão Consultar saldo: pergunta o saldo ao serviço do chatbot e o mostra num alerta, com a moeda.

        Se o serviço não responder ou devolver erro, o alerta traz a mensagem e a tela segue intacta."""
        self.ensure_one()
        if not self.env.user.has_group('cotacoes.group_quotation_administrator_user'):
            raise AccessError(_("Apenas administradores de cotações podem consultar o saldo do DeepSeek."))
        config = self.env['ir.config_parameter'].sudo()
        agent_url = (self.ai_agent_url or config.get_param('cotacoes.ai_agent_url', '') or '').strip().rstrip('/')
        agent_secret = self.ai_agent_secret or config.get_param('cotacoes.ai_agent_secret', '')
        try:
            balance = self._request_deepseek_balance(agent_url, agent_secret)
        except UserError as error:
            return self._balance_notification(error.args[0], 'warning')
        return self._balance_notification(balance, 'success')

    def _request_deepseek_balance(self, agent_url, agent_secret):
        """Saldo no formato '110.00 CNY'. Levanta UserError (mensagem clara) em qualquer falha."""
        if not agent_url:
            raise UserError(_("Informe a URL do Agente IA para consultar o saldo."))
        headers = {'X-Webhook-Secret': agent_secret} if agent_secret else {}
        try:
            response = requests.get(
                f'{agent_url}/internal/llm-balance',
                headers=headers,
                timeout=DEEPSEEK_BALANCE_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            _logger.warning("Consulta de saldo DeepSeek: serviço do chatbot sem resposta (%s)", type(error).__name__)
            raise UserError(_("Não foi possível consultar o saldo: o serviço do chatbot não respondeu."))
        if response.status_code != 200:
            raise UserError(self._deepseek_balance_error_message(response))
        try:
            payload = response.json()
            balances = [f"{info['total_balance']} {info['currency']}" for info in payload['balances']]
            is_available = payload['is_available']
        except (ValueError, KeyError, TypeError):
            raise UserError(_("Não foi possível consultar o saldo: resposta inesperada do serviço do chatbot."))
        if not balances:
            raise UserError(_("Não foi possível consultar o saldo: o serviço do chatbot não informou o saldo."))
        text = ' | '.join(balances)
        if not is_available:
            text += _(" (sem saldo disponível)")
        return text

    @api.model
    def _deepseek_balance_error_message(self, response):
        if response.status_code == 403:
            return _("Não foi possível consultar o saldo: o serviço do chatbot recusou o secret do Agente IA.")
        if response.status_code == 404:
            return _("Não foi possível consultar o saldo: o serviço do chatbot ainda não tem a consulta de saldo "
                     "(atualize o serviço).")
        try:
            detail = response.json().get('detail')
        except (ValueError, AttributeError):
            detail = None
        if isinstance(detail, str) and detail:
            return _("Não foi possível consultar o saldo: %(detail)s", detail=detail)
        return _("Não foi possível consultar o saldo: o serviço do chatbot respondeu com erro (HTTP %(status)s).",
                 status=response.status_code)

    @api.model
    def _balance_notification(self, message, level):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Créditos DeepSeek"),
                'message': message,
                'type': level,
                'sticky': True,
            },
        }

    @api.model
    def get_values(self):
        res = super().get_values()
        config = self.env['ir.config_parameter'].sudo()
        res.update({
            'ai_agent_url': config.get_param('cotacoes.ai_agent_url', 'http://localhost:8000'),
            'ai_agent_secret': config.get_param('cotacoes.ai_agent_secret', ''),
            'bot_idle_timeout_minutes': int(config.get_param('cotacoes.bot_idle_timeout_minutes', '5')),
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
