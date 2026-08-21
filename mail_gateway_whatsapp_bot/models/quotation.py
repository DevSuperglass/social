import datetime
import logging
import re

from odoo import api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_BOT_LOAD_GATEWAY_PHONE = '378316715373016'  # Gateway de INCLUSÃO
_BOT_LOAD_NOTIFY_MOBILE = '5511972345900'


class Quotation(models.Model):
    _inherit = 'quotation'

    @api.model_create_multi
    def create(self, vals_list):
        """Cria normalmente (date/due_date calculados pelo date_deadline()
        padrão do cotacoes) e, só pras cotações do bot criadas depois do
        horário de corte do cron de envio ao Hitec, dá um write simulando
        que a criação foi amanhã — só pra achar o próximo dia de rota certo,
        evitando que a cotação vença antes do ciclo de amanhã processá-la."""
        records = super().create(vals_list)

        bot_user = self.env.ref('mail_gateway_whatsapp_bot.superglassbot_user', raise_if_not_found=False)
        if not bot_user:
            return records

        for rec in records:
            if rec.vendor.id != bot_user.id or not rec.partner_route_id:
                continue
            if not rec._is_past_hitec_cutoff():
                continue
            next_day_route = rec.partner_route_id._compute_next_route_day_from(
                datetime.date.today() + datetime.timedelta(days=1)
            )
            rec.write({
                'date': next_day_route,
                'due_date': rec._compute_due_date_for(next_day_route),
            })

        return records

    def _is_past_hitec_cutoff(self):
        """Horário de corte lido dinamicamente do cron de envio ao Hitec —
        nextcall mantém sempre a mesma hora/minuto (UTC), avançando 1 dia a
        cada execução, então reflete o horário configurado no cron mesmo que
        ele mude no futuro."""
        cron = self.env.ref('mail_gateway_whatsapp_bot.cron_send_bot_quotations_to_hitec', raise_if_not_found=False)
        if not cron:
            return False
        cron = cron.sudo()
        if not cron.nextcall:
            return False
        return datetime.datetime.utcnow().time() > cron.nextcall.time()

    def _compute_due_date_for(self, next_day_route):
        """Mesma lógica de get_due_date() em cotacoes/quotation.py:
        date_deadline(), reimplementada aqui pra não alterar o módulo base
        (verticalização) — ajusta next_day_route pro dia útil/fds anterior
        conforme os horários da rota."""
        self.ensure_one()
        route = self.partner_route_id
        due_datetime = datetime.datetime.combine(next_day_route, datetime.time(0, 0))
        weekday = due_datetime.weekday()
        if weekday == 6:
            due_datetime += datetime.timedelta(days=-1, hours=route.weekend_day_time)
        elif weekday == 0:
            due_datetime += datetime.timedelta(days=-2, hours=route.weekend_day_time)
        else:
            due_datetime += datetime.timedelta(days=-1, hours=route.business_day_time)
        return due_datetime

    @api.model
    def _cron_confirm_bot_quotations(self):
        """Confirma automaticamente cotações do bot (vendor = SuperglassBot)
        com pelo menos um item confirmado e nenhum pendente — mesmo processo
        de confirm_quotation() usado manualmente, incluindo a criação de
        gerproc pro financeiro quando o valor excede o limite de crédito
        aprovado do cliente.

        Antes de confirmar, preenche payment_mode_id/payment_term_id a partir
        do cadastro do cliente (payment_term_partner): a única forma de
        pagamento cadastrada, e a condição de pagamento com o maior "Valor
        mínimo" que ainda seja menor ou igual ao total da cotação."""
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

            quotation._set_payment_mode_and_term_from_partner()

            try:
                quotation.confirm_quotation()
            except UserError as e:
                _logger.warning('Cron confirmação bot: cotação %s não confirmada: %s', quotation.id, e)

    def _set_payment_mode_and_term_from_partner(self):
        """Preenche payment_mode_id/payment_term_id com base no cadastro do
        cliente (object.payment.mode/object.payment.condition), sem
        sobrescrever o que já estiver preenchido."""
        self.ensure_one()
        partner = self.partner_id
        vals = {}

        if not self.payment_mode_id:
            payment_mode = partner.new_property_payment_mode_id.mapped(
                'rel_new_property_payment_mode_id'
            )[:1]
            if payment_mode:
                vals['payment_mode_id'] = payment_mode.id

        if not self.payment_term_id:
            payment_condition = partner.new_property_payment_term_id.filtered(
                lambda c: c.min_value <= self.total
            ).sorted(lambda c: c.min_value, reverse=True)[:1]
            if payment_condition.rel_new_property_payment_term_id:
                vals['payment_term_id'] = payment_condition.rel_new_property_payment_term_id.id

        if vals:
            self.write(vals)

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

    @api.model
    def _cron_send_bot_quotations_to_hitec(self):
        """Cria o pedido de venda (botão 'Criar Pedido') pras cotações do bot
        já confirmadas e ainda sem sale_order_id — mesmo processo manual de
        create_sale_order(), que confirma o sale.order e já dispara toda a
        cadeia existente até o Hitec (fatura → hitec_account_move._post() →
        _send_to_hitec() → database.request). Depende de payment_mode_id/
        payment_term_id já preenchidos (feito em _cron_confirm_bot_quotations).

        Roda como o próprio SuperglassBot (with_user): create_sale_order()
        usa self.env.uid como vendedor do pedido, e _send_to_hitec() exige um
        hr.employee com código Hitec vinculado a esse usuário — rodando como
        base.user_root (padrão do cron) isso sempre falhava."""
        bot_user = self.env.ref('mail_gateway_whatsapp_bot.superglassbot_user', raise_if_not_found=False)
        if not bot_user:
            return

        quotations = self.search([
            ('vendor', '=', bot_user.id),
            ('state', 'in', ('confirmed', 'approved_limit')),
            ('sale_order_id', '=', False),
        ])
        for quotation in quotations:
            lines = quotation.quotation_line_ids
            if not lines.filtered(lambda l: l.status == 'confirmed'):
                continue
            if lines.filtered(lambda l: l.status == 'pending'):
                continue
            try:
                quotation.with_user(bot_user).create_sale_order()
            except UserError as e:
                _logger.warning('Cron envio Hitec bot: cotação %s não convertida em pedido: %s', quotation.id, e)

    def _build_bot_load_message(self):
        """Monta o texto de pré-carregamento com os itens confirmados —
        campos do relatório 'Carregamento' (Produto, Ref. Fornecedor,
        Quantidade) mais Fabricante, Valor Unitário e Valor Total."""
        self.ensure_one()
        lines = self.quotation_line_ids.filtered(lambda l: l.status == 'confirmed')

        separator = "➖➖➖➖➖➖➖➖➖➖"
        items = []
        for i, line in enumerate(lines, 1):
            qty = line.quantity_corrected if line.quantity_corrected > 0 else line.quantity
            qty_clean = int(qty) if qty == int(qty) else qty
            ref = line.product_id.hitec_code.provider_ref or '-'
            fabricante = line.provider_name or '-'
            valor_unitario = line.revised_product_price
            valor_total = valor_unitario * qty
            items.append(
                f"{separator}\n"
                f"*{i}.* {line.product_id.name}\n"
                f"Fabricante: {fabricante}\n"
                f"Ref: {ref}\n"
                f"Quantidade: *{qty_clean}*\n"
                f"Valor Unitário: R$ {valor_unitario:.2f}\n"
                f"Valor Total: R$ {valor_total:.2f}"
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

            if button == 'CONFIRMAR' and quotation_id._is_past_hitec_cutoff():
                channel._dispatch_to_ai_agent(message, extra_payload={
                    'next_delivery_date': quotation_id.date.isoformat() if quotation_id.date else None,
                    'route_name': quotation_id.partner_route_id.nome_rota or None,
                })

        return result
