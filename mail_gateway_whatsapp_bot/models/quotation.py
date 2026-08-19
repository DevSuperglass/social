import logging
import re

from odoo import api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_BOT_LOAD_GATEWAY_PHONE = '378316715373016'  # Gateway de INCLUSÃO
_BOT_LOAD_NOTIFY_MOBILE = '5511972345900'  # Ricardo Ito


class Quotation(models.Model):
    _inherit = 'quotation'

    @api.model
    def _cron_confirm_bot_quotations(self):
        """Confirma automaticamente cotações do bot (vendor = SuperglassBot)
        com pelo menos um item confirmado e nenhum pendente — mesmo processo
        de confirm_quotation() usado manualmente, incluindo a criação de
        gerproc pro financeiro quando o valor excede o limite de crédito
        aprovado do cliente."""
        bot_user = self.env.ref('mail_gateway_whatsapp_bot.superglassbot_user', raise_if_not_found=False)
        if not bot_user:
            return

        quotations = self.search([
            ('vendor', '=', bot_user.id),
            ('state', '=', 'opened'),
        ])
        for quotation in quotations:
            lines = quotation.quotation_line_ids
            if not lines.filtered(lambda l: l.status == 'confirmed'):
                continue
            if lines.filtered(lambda l: l.status == 'pending'):
                continue
            try:
                quotation.confirm_quotation()
            except UserError as e:
                _logger.warning('Cron confirmação bot: cotação %s não confirmada: %s', quotation.id, e)

    @api.model
    def _cron_notify_bot_quotations_loaded(self):
        """Marca is_loaded=True nas cotações do bot já confirmadas e envia
        pro canal de INCLUSÃO o resumo dos itens confirmados até aqui, para
        pré-carregamento. A partir do is_loaded=True, novas confirmações na
        mesma cotação já são avisadas automaticamente pelo write() de
        quotation.line — este cron cobre só o envio inicial."""
        bot_user = self.env.ref('mail_gateway_whatsapp_bot.superglassbot_user', raise_if_not_found=False)
        if not bot_user:
            return

        quotations = self.search([
            ('vendor', '=', bot_user.id),
            ('state', 'in', ('confirmed', 'approved_limit')),
            ('is_loaded', '=', False),
        ])
        for quotation in quotations:
            if not quotation.quotation_line_ids.filtered(lambda l: l.status == 'confirmed'):
                continue
            quotation.set_is_loaded()
            message = quotation._build_bot_load_message()
            self.env['mail.gateway.whatsapp'].with_context(is_internal=True).send_tmpl_message(
                gateway_phone=_BOT_LOAD_GATEWAY_PHONE,
                tmpl_name=None,
                components=message,
                mobile_list=[_BOT_LOAD_NOTIFY_MOBILE],
                body_message=message,
            )

    def _build_bot_load_message(self):
        """Monta o texto de pré-carregamento com os itens confirmados —
        mesmos campos do relatório 'Carregamento' (Produto, Ref. Fornecedor,
        Quantidade), sem valores."""
        self.ensure_one()
        lines = self.quotation_line_ids.filtered(lambda l: l.status == 'confirmed')

        separator = "➖➖➖➖➖➖➖➖➖➖"
        items = []
        for i, line in enumerate(lines, 1):
            qty = line.quantity_corrected if line.quantity_corrected > 0 else line.quantity
            qty_clean = int(qty) if qty == int(qty) else qty
            ref = line.product_id.hitec_code.provider_ref or '-'
            items.append(
                f"{separator}\n"
                f"*{i}.* {line.product_id.name}\n"
                f"Ref: {ref}\n"
                f"Quantidade: *{qty_clean}*"
            )
        items.append(separator)

        rota = self.partner_route_id.nome_rota or '-'
        data_emissao = self.date.strftime('%d/%m/%Y') if self.date else '-'

        return (
            f"📦 *PRÉ-CARREGAMENTO*\n"
            f"Cotação Nº *{self.id}*\n"
            f"Cliente: {self.partner_id.display_name}\n"
            f"Rota: {rota}\n"
            f"Data de emissão: {data_emissao}\n\n" + "\n".join(items)
        )

    def item_verification_response(self):
        """Estende o comportamento padrão (grava status_map na linha): em
        CONFIRMAR/DESISTIR, também fecha a fila do bot e avisa o chatbot para
        limpar a sessão — mas só quando o canal do clique realmente está em
        atendimento bot (evita fechar a fila errada se o time de logística
        clicar nos mesmos botões no template validacao_produto, que usa outro
        canal)."""
        button = self.env.context.get('button')
        waid = self.env.context.get('waid')

        result = super().item_verification_response()

        if button not in ('CONFIRMAR', 'DESISTIR'):
            return result

        message = self.env['mail.message'].search([('whatsapp_id', '=', waid)])
        if not message:
            return result

        quotation_match = re.search(r'Cotação Nº: (\d+)', message.body or '')
        if not quotation_match:
            return result

        quotation_id = self.browse(int(quotation_match.group(1)))
        if quotation_id.state in ('fully_approved', 'expired', 'review'):
            # Mesmo gate do método base: não houve alteração de status real.
            return result

        channel = self.env['mail.channel'].browse(message.res_id)
        if channel.exists() and channel.attendance_type == 'bot':
            channel.delete_password_queue()
            channel._notify_chatbot_clear_session(channel.id)

        return result
