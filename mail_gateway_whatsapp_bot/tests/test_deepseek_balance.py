from unittest import mock

import requests

from odoo.exceptions import AccessError
from odoo.tests.common import Form, TransactionCase, tagged

REQUESTS_GET = 'odoo.addons.mail_gateway_whatsapp_bot.wizard.res_config.requests.get'
AGENT_URL = 'http://chatbot.test:8000'
AGENT_SECRET = 'segredo-de-teste'


def fake_response(status_code=200, payload=None, json_error=False):
    response = mock.Mock(status_code=status_code)
    if json_error:
        response.json.side_effect = ValueError('não é JSON')
    else:
        response.json.return_value = payload
    return response


def balance_payload(*balances, is_available=True):
    return {
        'is_available': is_available,
        'balances': [
            {'currency': currency, 'total_balance': total, 'granted_balance': '0.00', 'topped_up_balance': total}
            for currency, total in balances
        ],
    }


@tagged('post_install', '-at_install')
class TestDeepseekBalance(TransactionCase):
    """Botão Consultar saldo das definições do bot: o serviço do chatbot é sempre simulado."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group_system = cls.env.ref('base.group_system')
        group_quotation_admin = cls.env.ref('cotacoes.group_quotation_administrator_user')
        cls.admin = cls.env['res.users'].create({
            'name': 'Administrador de cotações',
            'login': 'admin_cotacoes_saldo',
            'groups_id': [(6, 0, [group_system.id, group_quotation_admin.id])],
        })
        cls.admin_without_quotations = cls.env['res.users'].create({
            'name': 'Administrador sem cotações',
            'login': 'admin_sem_cotacoes_saldo',
            'groups_id': [(6, 0, [group_system.id])],
        })

    def _open_settings(self, url=AGENT_URL, secret=AGENT_SECRET):
        """Abre as definições como o administrador, preenche URL e secret na tela e salva."""
        with Form(self.env['res.config.settings'].with_user(self.admin)) as form:
            form.ai_agent_url = url
            form.ai_agent_secret = secret
        return form.save()

    def _click_check_balance(self, response=None, side_effect=None, **screen):
        settings = self._open_settings(**screen)
        with mock.patch(REQUESTS_GET, return_value=response, side_effect=side_effect) as get:
            action = settings.action_check_deepseek_balance()
        return action, get

    def test_alerts_balance_with_currency_when_service_answers(self):
        action, _get = self._click_check_balance(fake_response(payload=balance_payload(('CNY', '110.00'))))

        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(action['params']['type'], 'success')
        self.assertEqual(action['params']['message'], '110.00 CNY')

    def test_asks_the_service_with_the_url_and_secret_of_the_screen(self):
        _action, get = self._click_check_balance(fake_response(payload=balance_payload(('CNY', '1.00'))))

        self.assertEqual(get.call_args.args[0], f'{AGENT_URL}/internal/llm-balance')
        self.assertEqual(get.call_args.kwargs['headers'], {'X-Webhook-Secret': AGENT_SECRET})
        self.assertTrue(get.call_args.kwargs['timeout'], 'A consulta precisa de timeout para a tela não ficar presa.')

    def test_alerts_every_currency_returned_by_the_service(self):
        action, _get = self._click_check_balance(
            fake_response(payload=balance_payload(('CNY', '110.00'), ('USD', '5.00'))))

        self.assertEqual(action['params']['message'], '110.00 CNY | 5.00 USD')

    def test_flags_when_account_has_no_available_balance(self):
        action, _get = self._click_check_balance(
            fake_response(payload=balance_payload(('CNY', '0.00'), is_available=False)))

        self.assertEqual(action['params']['message'], '0.00 CNY (sem saldo disponível)')

    def test_warns_instead_of_breaking_the_screen_when_the_service_fails(self):
        # Agrupado com subTest: as 9 falhas têm o mesmo resultado esperado (alerta de aviso, sem exceção) e abrir as
        # definições com Form custa ~0,45 s por cenário (medido); separar os 9 testes gastaria ~3,6 s só repetindo isso.
        failures = {
            'service down': dict(side_effect=requests.ConnectionError('recusou')),
            'timeout': dict(side_effect=requests.Timeout('lento')),
            'bad secret': dict(response=fake_response(status_code=403)),
            'old service': dict(response=fake_response(status_code=404)),
            'deepseek error': dict(
                response=fake_response(status_code=502, payload={'detail': 'O DeepSeek não respondeu.'})),
            'error without body': dict(response=fake_response(status_code=500, json_error=True)),
            'answer not json': dict(response=fake_response(json_error=True)),
            'answer without balances': dict(response=fake_response(payload={'is_available': True})),
            'answer with empty balances': dict(response=fake_response(payload=balance_payload())),
        }
        for name, kwargs in failures.items():
            with self.subTest(failure=name):
                action, _get = self._click_check_balance(**kwargs)

                self.assertEqual(action['tag'], 'display_notification')
                self.assertEqual(action['params']['type'], 'warning', 'Deveria avisar o usuário.')
                self.assertIn('Não foi possível consultar o saldo', action['params']['message'])

    def test_tells_the_service_message_when_it_returns_an_error_detail(self):
        action, _get = self._click_check_balance(
            fake_response(status_code=502, payload={'detail': 'O DeepSeek não respondeu.'}))

        self.assertIn('O DeepSeek não respondeu.', action['params']['message'])

    def test_warns_to_fill_the_url_without_calling_the_service_when_url_is_empty(self):
        self.env['ir.config_parameter'].sudo().set_param('cotacoes.ai_agent_url', '')

        action, get = self._click_check_balance(url=False)

        self.assertEqual(action['params']['type'], 'warning')
        self.assertIn('URL do Agente IA', action['params']['message'])
        get.assert_not_called()

    def test_refuses_user_outside_the_quotation_administrators_group(self):
        settings = self.env['res.config.settings'].with_user(self.admin_without_quotations).create({
            'ai_agent_url': AGENT_URL,
            'ai_agent_secret': AGENT_SECRET,
        })

        with mock.patch(REQUESTS_GET) as get, self.assertRaises(AccessError):
            settings.action_check_deepseek_balance()

        get.assert_not_called()
