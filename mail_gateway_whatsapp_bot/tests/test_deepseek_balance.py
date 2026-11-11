from unittest import mock

import requests

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged

REQUESTS_GET = 'odoo.addons.mail_gateway_whatsapp_bot.wizard.res_config.requests.get'
BALANCE_PARAM = 'cotacoes.deepseek_balance'
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
        cls.config = cls.env['ir.config_parameter'].sudo()
        cls.config.set_param(BALANCE_PARAM, '')
        group_system = cls.env.ref('base.group_system')
        group_quotation_admin = cls.env.ref('cotacoes.group_quotation_administrator_user')
        cls.admin = cls.env['res.users'].create({
            'name': 'Administrador de cotações',
            'login': 'admin_cotacoes_saldo',
            'groups_id': [(6, 0, [group_system.id, group_quotation_admin.id])],
        })
        cls.other_admin = cls.env['res.users'].create({
            'name': 'Administrador sem cotações',
            'login': 'admin_sem_cotacoes_saldo',
            'groups_id': [(6, 0, [group_system.id])],
        })

    def _settings(self, user=None, url=AGENT_URL, secret=AGENT_SECRET):
        settings = self.env['res.config.settings'].with_user(user or self.admin).create({
            'ai_agent_url': url,
            'ai_agent_secret': secret,
        })
        return settings

    def _check(self, response=None, side_effect=None, **settings_kwargs):
        settings = self._settings(**settings_kwargs)
        with mock.patch(REQUESTS_GET, return_value=response, side_effect=side_effect) as get:
            action = settings.action_check_deepseek_balance()
        return settings, action, get

    def test_shows_balance_with_currency_and_stores_it_when_service_answers(self):
        settings, action, get = self._check(fake_response(payload=balance_payload(('CNY', '110.00'))))

        self.assertEqual(settings.deepseek_balance, '110.00 CNY')
        self.assertEqual(self.config.get_param(BALANCE_PARAM), '110.00 CNY')
        self.assertEqual(action['params']['type'], 'success')
        self.assertEqual(action['params']['next']['tag'], 'soft_reload')

    def test_asks_the_service_with_the_url_and_secret_of_the_screen(self):
        _settings, _action, get = self._check(fake_response(payload=balance_payload(('CNY', '1.00'))))

        self.assertEqual(get.call_args.args[0], f'{AGENT_URL}/internal/llm-balance')
        self.assertEqual(get.call_args.kwargs['headers'], {'X-Webhook-Secret': AGENT_SECRET})
        self.assertTrue(get.call_args.kwargs['timeout'])

    def test_shows_every_currency_returned_by_the_service(self):
        settings, _action, _get = self._check(fake_response(payload=balance_payload(('CNY', '110.00'), ('USD', '5.00'))))

        self.assertEqual(settings.deepseek_balance, '110.00 CNY | 5.00 USD')

    def test_flags_when_account_has_no_available_balance(self):
        settings, _action, _get = self._check(
            fake_response(payload=balance_payload(('CNY', '0.00'), is_available=False)))

        self.assertEqual(settings.deepseek_balance, '0.00 CNY (sem saldo disponível)')

    def test_keeps_last_balance_and_warns_when_service_fails(self):
        failures = {
            'service down': dict(side_effect=requests.ConnectionError('recusou')),
            'timeout': dict(side_effect=requests.Timeout('lento')),
            'bad secret': dict(response=fake_response(status_code=403)),
            'old service': dict(response=fake_response(status_code=404)),
            'deepseek error': dict(response=fake_response(status_code=502, payload={'detail': 'O DeepSeek não respondeu.'})),
            'error without body': dict(response=fake_response(status_code=500, json_error=True)),
            'answer not json': dict(response=fake_response(json_error=True)),
            'answer without balances': dict(response=fake_response(payload={'is_available': True})),
            'answer with empty balances': dict(response=fake_response(payload=balance_payload())),
        }
        for name, kwargs in failures.items():
            with self.subTest(failure=name):
                self.config.set_param(BALANCE_PARAM, '50.00 CNY')

                settings, action, _get = self._check(**kwargs)

                self.assertEqual(action['params']['type'], 'warning', 'Deveria avisar o usuário.')
                self.assertTrue(action['params']['sticky'])
                self.assertIn('Não foi possível consultar o saldo', action['params']['message'])
                self.assertNotIn('next', action['params'], 'Erro não deve recarregar a tela.')
                self.assertEqual(settings.deepseek_balance, '50.00 CNY', 'Deveria manter o último saldo.')
                self.assertEqual(self.config.get_param(BALANCE_PARAM), '50.00 CNY')

    def test_tells_the_service_message_when_it_returns_an_error_detail(self):
        _settings, action, _get = self._check(
            fake_response(status_code=502, payload={'detail': 'O DeepSeek não respondeu.'}))

        self.assertIn('O DeepSeek não respondeu.', action['params']['message'])

    def test_warns_to_fill_the_url_without_calling_the_service_when_url_is_empty(self):
        self.config.set_param('cotacoes.ai_agent_url', '')

        _settings, action, get = self._check(url=False)

        self.assertEqual(action['params']['type'], 'warning')
        self.assertIn('URL do Agente IA', action['params']['message'])
        get.assert_not_called()

    def test_refuses_user_outside_the_quotation_administrators_group(self):
        settings = self._settings(user=self.other_admin)

        with mock.patch(REQUESTS_GET) as get, self.assertRaises(AccessError):
            settings.action_check_deepseek_balance()

        get.assert_not_called()

    def test_field_shows_the_last_balance_when_the_screen_is_opened_again(self):
        self.config.set_param(BALANCE_PARAM, '80.00 CNY')

        settings = self._settings()

        self.assertEqual(settings.deepseek_balance, '80.00 CNY')
