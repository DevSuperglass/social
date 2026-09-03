import datetime
import logging
import re

import pytz

from odoo import api, fields, models
from odoo.exceptions import UserError

_BUSINESS_TZ = pytz.timezone('America/Sao_Paulo')

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
        que a criação foi amanhã — hoje já era, o mais cedo que essa
        cotação pode ser processada é o ciclo de amanhã, então date/
        due_date precisam refletir isso: mesma regra de qualquer cotação
        (próximo dia de rota A PARTIR do dia de criação), só que aplicada
        a partir do dia de criação SIMULADO (amanhã), não do dia real de
        hoje — por isso o +1 é somado ANTES de chamar
        _compute_next_route_day_from (que soma outro +1 internamente,
        igual calcula_vencimento/get_due_date faz a partir de "hoje")."""
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

    def _is_past_cutoff(self, config_prefix, default_weekday, default_saturday):
        """Verifica se já passou do horário de corte (Brasília) de HOJE pra
        um cron do bot (`config_prefix`), considerando dia útil (segunda a
        sexta) x sábado — configurável em Configurações > Cotações >
        Horários (cotacoes.{config_prefix}_weekday/_saturday).
        Não lê cron.nextcall: como sábado pode ter horário diferente do dia
        útil, nextcall passou a variar por dia da semana (reflete a PRÓXIMA
        execução, não necessariamente o horário de HOJE) — calcular direto
        da config evita essa inconsistência.
        Sem cron aos domingos: trata domingo como sempre "passado do corte",
        caindo no próximo dia útil de rota."""
        today = datetime.datetime.now(pytz.utc).astimezone(_BUSINESS_TZ).date()
        if today.weekday() == 6:
            return True
        hour_brasilia = self._get_bot_cron_hour(
            config_prefix, default_weekday, default_saturday, today.weekday()
        )
        cutoff_time = self._brasilia_hour_to_utc_time(hour_brasilia)
        return datetime.datetime.utcnow().time() > cutoff_time

    def _is_past_confirm_cutoff(self):
        """Horário de corte de HOJE pro cron de confirmação automática
        (cron_hour_confirm) — ver _is_past_cutoff."""
        return self._is_past_cutoff('cron_hour_confirm', 16.0, 16.0)

    def _is_past_notify_loaded_cutoff(self):
        """Horário de corte de HOJE pro cron de aviso de carregamento
        (cron_hour_notify_loaded) — ver _is_past_cutoff."""
        return self._is_past_cutoff('cron_hour_notify_loaded', 16.25, 16.25)

    def _is_past_hitec_cutoff(self):
        """Horário de corte de HOJE pro cron de envio ao Hitec
        (cron_hour_send_hitec) — ver _is_past_cutoff."""
        return self._is_past_cutoff('cron_hour_send_hitec', 16.5, 16.5)

    def _get_bot_cron_hour(self, config_prefix, default_weekday, default_saturday, weekday):
        """Horário (Brasília) configurado pra um cron do bot no dia de semana
        informado (`weekday`: 0=segunda ... 5=sábado, 6=domingo) — dia útil x
        sábado têm chaves de config separadas."""
        config = self.env['ir.config_parameter'].sudo()
        suffix = 'saturday' if weekday == 5 else 'weekday'
        default = default_saturday if weekday == 5 else default_weekday
        return float(config.get_param(f'cotacoes.{config_prefix}_{suffix}', default))

    @staticmethod
    def _brasilia_hour_to_utc_time(hour_brasilia):
        """Converte hora (Brasília, float — ex: 16.25 = 16h15) pra datetime.time
        UTC. Offset fixo -3h: Brasil não observa horário de verão desde 2019."""
        hour_utc = (hour_brasilia + 3) % 24
        hh = int(hour_utc)
        mm = int(round((hour_utc - hh) * 60))
        return datetime.time(hh, mm)

    @staticmethod
    def _utc_naive_to_brasilia_date(value):
        """Converte um Datetime UTC naive (como vem do ORM, ex.:
        hitec_send_date) pra date em Brasília — usado em
        item_verification_response() pra saber se um item confirmado
        pertence a uma cotação finalizada HOJE (mesmo dia) ou em outro
        dia, e assim decidir entre simular "amanhã" ou usar a data de
        hoje diretamente (ver _recover_confirmed_item)."""
        return pytz.utc.localize(value).astimezone(_BUSINESS_TZ).date()

    def _bot_cron_call_for_day(self, target_day, config_prefix, default_weekday, default_saturday):
        """Data/hora (UTC) pro cron do bot no dia informado — pula domingo
        (empurra pra segunda-feira, no horário de dia útil). Base
        compartilhada por _next_bot_cron_call (reagendamento imediato ao
        salvar a config) e por _cron_schedule_bot_daily_hours (ajuste diário
        dos 3 crons pro dia corrente)."""
        while target_day.weekday() == 6:
            target_day += datetime.timedelta(days=1)
        hour_brasilia = self._get_bot_cron_hour(
            config_prefix, default_weekday, default_saturday, target_day.weekday()
        )
        return datetime.datetime.combine(target_day, self._brasilia_hour_to_utc_time(hour_brasilia))

    def _next_bot_cron_call(self, config_prefix, default_weekday, default_saturday):
        """Próxima data/hora (UTC, a partir de amanhã) de execução de um cron
        do bot — usado só pelo reagendamento imediato ao salvar a config
        (res_config.py). O ajuste diário de rotina é feito por
        _cron_schedule_bot_daily_hours, não por autorreagendamento no fim de
        cada cron (ver motivo no docstring dele).

        "Hoje"/"amanhã" calculados explicitamente em America/Sao_Paulo (via
        pytz), não datetime.date.today() — este último usa o fuso do SO do
        servidor, que pode não ser Brasília (ex: servidor configurado em UTC),
        levando a classificar o dia de semana errado perto da virada."""
        today_brasilia = datetime.datetime.now(pytz.utc).astimezone(_BUSINESS_TZ).date()
        return self._bot_cron_call_for_day(
            today_brasilia + datetime.timedelta(days=1),
            config_prefix, default_weekday, default_saturday,
        )

    def _reschedule_bot_cron(self, xmlid, config_prefix, default_weekday, default_saturday):
        """Reagenda o nextcall de um cron do bot pra próxima ocorrência —
        chamado imediatamente quando o horário é alterado em Configurações >
        Cotações > Horários (contexto seguro: roda fora da execução do
        próprio cron, sem conflito de lock — ver _cron_schedule_bot_daily_hours)."""
        cron = self.env.ref(xmlid, raise_if_not_found=False)
        if not cron:
            return
        cron.sudo().write({
            'nextcall': self._next_bot_cron_call(config_prefix, default_weekday, default_saturday)
        })

    @api.model
    def _cron_schedule_bot_daily_hours(self):
        """Roda 1x por dia, num horário FIXO e não-configurável (bem cedo —
        ver data/quotation_crons.xml), e ajusta o nextcall dos 3 crons
        configuráveis do bot (confirmação, aviso de carregamento, envio ao
        Hitec) pro horário de HOJE (dia útil x sábado, config em
        Configurações > Cotações > Horários).

        Não são os próprios 3 crons que se reagendam sozinhos ao final da
        execução: ir.cron.write() sempre chama _try_lock() (SELECT ... FOR NO
        KEY UPDATE NOWAIT) antes de gravar, pra impedir alteração de um cron
        enquanto ele está em execução — e um cron tentando escrever nele mesmo
        durante a própria execução colide com esse lock (LockNotAvailable) e
        derruba o job inteiro, caindo no reagendamento padrão do Odoo (errado,
        não considera dia útil x sábado). Rodando num horário fixo e bem
        anterior ao de qualquer um dos 3, este cron nunca concorre com eles
        (nenhum está em execução nesse momento), então o write() normal
        funciona sem conflito."""
        today_brasilia = datetime.datetime.now(pytz.utc).astimezone(_BUSINESS_TZ).date()
        for xmlid, config_prefix, default_weekday, default_saturday in (
            ('mail_gateway_whatsapp_bot.cron_confirm_bot_quotations', 'cron_hour_confirm', 16.0, 16.0),
            ('mail_gateway_whatsapp_bot.cron_notify_bot_quotations_loaded', 'cron_hour_notify_loaded', 16.25, 16.25),
            ('mail_gateway_whatsapp_bot.cron_send_bot_quotations_to_hitec', 'cron_hour_send_hitec', 16.5, 16.5),
        ):
            cron = self.env.ref(xmlid, raise_if_not_found=False)
            if not cron:
                continue
            cron.sudo().write({
                'nextcall': self._bot_cron_call_for_day(
                    today_brasilia, config_prefix, default_weekday, default_saturday
                )
            })

    def _compute_due_date_for(self, next_day_route):
        """Idêntico a get_due_date() em cotacoes/quotation.py:
        date_deadline() — reimplementado aqui pra não alterar o módulo base
        (verticalização), mesmo cálculo, byte a byte. Ajusta next_day_route
        pro dia útil/fds anterior conforme os horários da rota.

        Igual ao original: retorna o datetime "naive" direto, sem localizar/
        converter fuso — é assim que o due_date de uma cotação criada
        normalmente (via date_deadline(), sem passar por aqui) também é
        gravado, então precisa bater byte a byte com esse comportamento pra
        não divergir do valor que uma cotação "normal" (mesmo dia) teria."""
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
        payment_term_id já preenchidos (feito em _cron_confirm_bot_quotations
        ou na "janela" de item_verification_response) — se a cotação chegou
        até aqui sem isso, o UserError de create_sale_order() é só logado
        (não interrompe o cron pras demais cotações), sinalizando um
        problema real a investigar, não corrigido silenciosamente aqui.

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

    def _maybe_send_to_hitec_after_gerproc_approval(self):
        """Chamado por project_request.approve_quote() (aprovação do gerproc,
        em mail_gateway_whatsapp_bot/models/project_request.py) quando a
        cotação aprovada é do bot. Se a aprovação aconteceu depois do
        horário de corte do cron de envio ao Hitec do dia, dispara o envio
        direto — mesmo processo de _cron_send_bot_quotations_to_hitec — em
        vez de deixar a cotação esperando o próximo ciclo do cron, no dia
        seguinte."""
        bot_user = self.env.ref('mail_gateway_whatsapp_bot.superglassbot_user', raise_if_not_found=False)
        if not bot_user:
            return

        for quotation in self:
            if quotation.vendor.id != bot_user.id:
                continue
            if quotation.sale_order_id:
                continue
            if not quotation._is_past_hitec_cutoff():
                continue
            lines = quotation.quotation_line_ids
            if not lines.filtered(lambda l: l.status == 'confirmed'):
                continue
            if lines.filtered(lambda l: l.status == 'pending'):
                continue
            # Garante payment_mode_id/payment_term_id mesmo que a cotação
            # tenha chegado em approved_limit por um caminho que não passou
            # por _cron_confirm_bot_quotations nem pela "janela" de
            # item_verification_response (únicos dois pontos que hoje
            # chamam isso antes de confirm_quotation()) — sem isso,
            # create_sale_order() abaixo levanta UserError (capturado e só
            # logado, nunca visível) e a cotação nunca sai do lugar.
            quotation._set_payment_mode_and_term_from_partner()
            try:
                quotation.with_user(bot_user).create_sale_order()
            except UserError as e:
                _logger.warning(
                    'Envio Hitec pós-aprovação gerproc: cotação %s não convertida em pedido: %s',
                    quotation.id, e,
                )

    def _resolve_item_to_quotation(self, line, target_date=None, target_due_date=None):
        """Copia pra outra cotação (nova ou já aberta do mesmo cliente/
        vendedor) o item que acabou de ser confirmado — usado quando a
        cotação original não pode mais receber esse item: já foi
        finalizada (item órfão) ou a confirmação chegou depois do corte
        de hoje pro envio ao Hitec (precisa de data de amanhã) — ver
        item_verification_response.

        COPIA a linha (não move/reparenta) — a linha original permanece
        intacta na cotação de origem, que é histórico e não deve ser
        alterado (produto, quantidade, preço, destinatário preservados
        pelo copy() padrão). Quem chama decide o que fazer com o status da
        linha original (ver _recover_confirmed_item).

        Reaproveita uma cotação já aberta do mesmo cliente/vendedor, se
        houver — mesmo critério que o chatbot usa ao criar linha nova
        (chatbot/database.py, create_quotation_line: state not in
        ('expired', 'fully_approved')) — em vez de criar uma cotação nova
        a cada item. Quando target_date é informado (cenário "amanhã"),
        restringe a busca a cotações que já tenham essa mesma data, pra
        não misturar um item de amanhã numa cotação aberta de hoje. Sem
        target_date (cenário "hoje"/item órfão), não restringe por data —
        mesmo comportamento livre que o chatbot já usa.

        Cotação nova (quando precisa criar): sem target_date, passa pelo
        create() normal (date/due_date calculados do zero por
        date_deadline(), refletindo hoje). Com target_date, grava
        date/due_date explícitos.

        Roda como o próprio SuperglassBot (with_user) — self.vendor já é
        o bot nos dois cenários que chamam este método (item órfão e
        confirmação tardia já conferem vendor == bot antes de chegar
        aqui) — pra criação/alteração ficarem atribuídas a ele no
        chatter, não a quem processou o webhook.

        Retorna (target_quotation, new_line) — new_line é a cópia recém
        criada na cotação de destino."""
        self.ensure_one()
        bot_user = self.vendor
        domain = [
            ('partner_id', '=', self.partner_id.id),
            ('vendor', '=', bot_user.id),
            ('state', 'not in', ('expired', 'fully_approved')),
            ('id', '!=', self.id),
        ]
        if target_date:
            domain.append(('date', '=', target_date))
        target_quotation = self.with_user(bot_user).search(domain, order='create_date desc', limit=1)

        if not target_quotation:
            # Mesmo padrão usado pelo chatbot ao criar cotação nova
            # (chatbot/database.py, create_quotation_line): state explícito
            # na criação, e name (número da cotação, "COTACAOxx") gravado à
            # parte depois — não é preenchido sozinho, e o form/relatórios
            # usam o campo em si, não name_get().
            vals = {
                'partner_id': self.partner_id.id,
                'vendor': bot_user.id,
                'state': 'opened',
            }
            if target_date:
                vals['date'] = target_date
                vals['due_date'] = target_due_date
            target_quotation = self.env['quotation'].with_user(bot_user).create(vals)
            target_quotation.with_user(bot_user).write({'name': "COTACAO{}".format(target_quotation.id)})

        new_line = line.with_user(bot_user).copy({'quotation_id': target_quotation.id})
        return target_quotation, new_line

    def _notify_client_item_moved(self, line, new_quotation, reason):
        """Avisa o cliente (recipient_id da linha) que a confirmação do
        produto foi registrada, mas caiu numa cotação diferente da
        original (ver _resolve_item_to_quotation). reason='orphan':
        pedido de origem já tinha sido processado (finalizado em outro
        dia). reason='next_delivery': confirmação chegou depois do corte
        de hoje pro envio ao Hitec, item vai pra próxima entrega. Falha
        ao notificar (ex.: telefone inválido) nunca deve derrubar a
        transação — o item já foi movido/confirmado com sucesso nesse
        ponto."""
        self.ensure_one()
        try:
            mobile = line.recipient_id.phone_sanitized
            if not mobile:
                return
            mobile = mobile.split('+')[1]
            if reason == 'next_delivery':
                date_str = new_quotation.date.strftime('%d/%m/%Y') if new_quotation.date else '-'
                body = (
                    f"A confirmação do produto {line.product_id.name} foi registrada "
                    f"com sucesso! Em razão do horário, ela será processada na "
                    f"próxima entrega: {date_str} (Cotação Nº {new_quotation.id})."
                )
            else:
                body = (
                    f"A confirmação do produto {line.product_id.name} foi registrada "
                    f"com sucesso! Como o pedido anterior já havia sido processado, "
                    f"esse item foi incluído automaticamente na Cotação Nº "
                    f"{new_quotation.id}."
                )
            self.env['mail.gateway.whatsapp'].with_context(is_internal=True).send_tmpl_message(
                gateway_phone='335789752960181',
                tmpl_name=None,
                components=body,
                mobile_list=[mobile],
                body_message=body,
            )
        except Exception:
            _logger.exception(
                'Falha ao notificar cliente sobre item %s movido pra cotação %s (motivo=%s)',
                line.id, new_quotation.id, reason,
            )

    def _recover_confirmed_item(self, line, simulate_tomorrow):
        """Move um item confirmado que não pode (mais) ficar na cotação
        original para uma cotação nova/reaproveitada — chamado de dois
        pontos de item_verification_response, que já decidem e passam
        `simulate_tomorrow` de acordo com o cenário (ver lá):

        - simulate_tomorrow=True ("confirmação após o horário, mesmo dia"):
          hoje já era — cria/reaproveita cotação simulando que a criação
          foi amanhã (mesma lógica de create()), pra date/due_date
          refletirem o próximo dia de rota A PARTIR de amanhã, não de
          hoje — reason 'next_delivery'.
        - simulate_tomorrow=False ("item de cotação já finalizada em dia
          diferente, ou ainda dentro do horário de hoje"): cria/reaproveita
          com a data de HOJE (dia da própria confirmação), sem simular
          nada — reason 'orphan'.

        A linha original NUNCA é removida/reparentada (ver
        _resolve_item_to_quotation) — fica intacta na cotação de origem,
        como histórico. Só ajusta o status dela se estava 'confirmed'
        (cenário "mesmo dia, ainda aberta": super() já tinha gravado isso
        antes): regrava pra 'givenup', senão a cotação de origem ficaria
        com uma linha confirmada que os crons de hoje poderiam processar
        de novo, duplicando o envio do mesmo item."""
        self.ensure_one()
        bot_user = self.vendor
        if simulate_tomorrow:
            route = self.partner_route_id
            # Hoje já era — simula que a criação foi amanhã (mesma lógica
            # de create()): soma +1 ANTES de chamar
            # _compute_next_route_day_from, que soma outro +1 internamente
            # a partir desse dia simulado.
            next_day_route = route._compute_next_route_day_from(
                fields.Date.context_today(self) + datetime.timedelta(days=1)
            )
            due_date = self._compute_due_date_for(next_day_route)
            target_quotation, new_line = self._resolve_item_to_quotation(
                line, target_date=next_day_route, target_due_date=due_date,
            )
            reason = 'next_delivery'
        else:
            target_quotation, new_line = self._resolve_item_to_quotation(line)
            reason = 'orphan'

        new_line.with_user(bot_user).write({'status': 'confirmed'})
        if line.status == 'confirmed':
            line.with_user(bot_user).write({'status': 'givenup'})
        self._notify_client_item_moved(new_line, target_quotation, reason=reason)
        return target_quotation

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
        canal). Cotação de vendedor humano (ou qualquer coisa fora dos 2
        cenários abaixo) segue o comportamento padrão do cotacoes, sem
        nenhuma interferência daqui.

        Além disso, trata 2 cenários específicos de cotação do bot em que
        CONFIRMAR normal não é suficiente — em ambos, o item é movido pra
        uma cotação nova/reaproveitada via _recover_confirmed_item, que
        decide a data (hoje ou amanhã) só pelo horário atual em relação ao
        corte de envio ao Hitec, não por comparação de dias:

        - Cotação já fully_approved (foi enviada ao Hitec — create_sale_order
          processa os itens que já estavam confirmados nesse momento; o
          cron de envio só exige ausência de status 'pending', não
          considera itens ainda 'waiting'). O guard padrão de
          item_verification_response (cotacoes, baseado só no state da
          cotação) bloquearia com uma mensagem enganosa ("está em
          processamento") e nunca gravaria nada — nem quando o cliente
          confirma ainda no MESMO dia em que a cotação foi enviada, então
          não dá pra usar comparação de dia como critério aqui.

        - Cotação ainda aberta, mas a confirmação chegou depois do corte de
          envio ao Hitec de hoje: o cron de envio já passou — mesmo
          confirmando agora, só seria pega pelo cron de amanhã, mas com a
          data de hoje gravada (errada pro que de fato vai ser processado
          amanhã).

        Fora desses dois casos, mas ainda dentro da janela entre o corte de
        confirmação e o corte de envio ao Hitec de hoje, confirma a cotação
        direto (mais abaixo) — o cron de confirmação já rodou e não roda de
        novo, e o de envio ao Hitec só pega cotação já confirmada."""
        button = self.env.context.get('button')
        waid = self.env.context.get('waid')

        message = self.env['mail.message'].search([('whatsapp_id', '=', waid)])
        quotation_id = self.env['quotation']
        product_name = ''
        provider_name = ''
        if message:
            quotation_match = re.search(r'Cotação Nº: (\d+)', message.body or '')
            if quotation_match:
                quotation_id = self.browse(int(quotation_match.group(1)))
            product_match = re.search(r'Produto: (.+?)(?=\n|<)', message.body or '')
            product_name = product_match.group(1) if product_match else ''
            provider_match = re.search(r'Fabricante: (.+?)(?=\n|<)', message.body or '')
            provider_name = provider_match.group(1) if provider_match else ''

        bot_user = self.env.ref('mail_gateway_whatsapp_bot.superglassbot_user', raise_if_not_found=False)
        is_bot_quotation = bool(bot_user and quotation_id and quotation_id.vendor.id == bot_user.id)

        # Cotação do bot já fully_approved OU expired: super() nem chegaria
        # a gravar o status — o guard de state bloquearia antes. Recupera o
        # item direto daqui.
        orphan_line = self.env['quotation.line']
        orphan_target_quotation = self.env['quotation']
        if button == 'CONFIRMAR' and is_bot_quotation and quotation_id.state in ('fully_approved', 'expired'):
            candidate_line = quotation_id.quotation_line_ids.with_context(lang="en_US").filtered(
                lambda ql: ql.product_id.name == product_name
                and ql.product_id.conf_provider_id.provider_id.name == provider_name
            )
            if candidate_line:
                # expired: sempre cenário 3 — cria/reaproveita com a data
                # de HOJE (dia da confirmação), nunca simula amanhã. Não
                # reaproveita a comparação por hitec_send_date usada no
                # fully_approved (só esse cenário está sendo tratado agora
                # pro caso expired).
                #
                # fully_approved: simulate_tomorrow só é True se a cotação
                # foi finalizada HOJE (mesmo dia da confirmação) — item
                # confirmado depois do corte de hoje, mesma regra de
                # "confirmação tardia" (linha ~679). Se foi finalizada em
                # outro dia (ontem ou antes), a data usada é a de HOJE (dia
                # da confirmação), sem simular nada, independente de já ter
                # passado do corte de hoje ou não.
                if quotation_id.state == 'expired':
                    simulate_tomorrow = False
                else:
                    simulate_tomorrow = bool(
                        quotation_id.hitec_send_date
                        and self._utc_naive_to_brasilia_date(quotation_id.hitec_send_date)
                        == self._utc_naive_to_brasilia_date(fields.Datetime.now())
                    )
                # _recover_confirmed_item copia a linha pra cotação
                # nova/reaproveitada (já gravando confirmed na cópia) — a
                # linha original permanece intacta, como histórico, na
                # cotação antiga já finalizada/expirada.
                orphan_target_quotation = quotation_id._recover_confirmed_item(candidate_line, simulate_tomorrow)
                orphan_line = candidate_line

        result = True if orphan_line else super().item_verification_response()

        if button not in ('CONFIRMAR', 'DESISTIR'):
            return result

        if not message or not quotation_id:
            return result

        if not orphan_line and quotation_id.state in ('fully_approved', 'expired', 'review'):
            # Mesmo gate do método base: não houve alteração de status real.
            return result

        # A partir daqui, "target_quotation" é a cotação que de fato tem o
        # item que acabou de ser confirmado — normalmente a própria
        # quotation_id, mas pode virar uma cotação nova/reaproveitada nos
        # blocos abaixo (ou já ser a cotação da recuperação, tratada acima).
        # "confirmed_line" identifica esse item (mesmo matching de
        # produto+fabricante do método base) — super() (ou a recuperação
        # acima) já gravou status='confirmed' nele antes da gente chegar
        # aqui.
        target_quotation = orphan_target_quotation if orphan_line else quotation_id
        confirmed_line = orphan_line
        if button == 'CONFIRMAR' and not confirmed_line:
            confirmed_line = quotation_id.quotation_line_ids.with_context(lang="en_US").filtered(
                lambda ql: ql.product_id.name == product_name
                and ql.product_id.conf_provider_id.provider_id.name == provider_name
                and ql.status == 'confirmed'
            )

        if button == 'CONFIRMAR' and confirmed_line and not orphan_line and is_bot_quotation:
            if target_quotation._is_past_hitec_cutoff():
                # Confirmação tardia: já passou do corte de envio ao Hitec
                # de hoje — mesmo confirmando agora, só seria pega pelo
                # cron de amanhã, mas com a data de hoje gravada (errada
                # pro que vai ser processado amanhã). Sempre "mesmo dia"
                # aqui (avaliado em tempo real, a cotação ainda estava
                # aberta até agora) — sempre simula amanhã.
                target_quotation = quotation_id._recover_confirmed_item(confirmed_line[0], simulate_tomorrow=True)

            elif target_quotation._is_past_confirm_cutoff():
                # Janela entre o corte de confirmação e o corte de envio ao
                # Hitec: o cron de confirmação (mais cedo) já rodou e não
                # roda de novo hoje, e o cron de envio ao Hitec (mais
                # tarde) ainda não passou — confirma direto, mesmas
                # condições do cron (_cron_confirm_bot_quotations), pra ele
                # já achar a cotação pronta.
                lines = target_quotation.quotation_line_ids
                if lines.filtered(lambda l: l.status == 'confirmed') and not lines.filtered(lambda l: l.status == 'pending'):
                    target_quotation._set_payment_mode_and_term_from_partner()
                    confirmed_now = False
                    try:
                        target_quotation.confirm_quotation()
                        confirmed_now = True
                    except UserError as e:
                        _logger.warning(
                            'Confirmação imediata (janela confirm→hitec): cotação %s não confirmada: %s',
                            target_quotation.id, e,
                        )

                    # Distinção entre a subjanela confirm→notify_loaded (só
                    # confirma, o cron de aviso de carregamento ainda vai
                    # rodar hoje e pega essa cotação normalmente) e
                    # notify_loaded→hitec (o cron de aviso já rodou e NÃO
                    # pegou essa cotação, porque ela ainda não estava
                    # confirmada nesse momento — sem isso, o aviso só
                    # sairia amanhã). Nesse segundo caso, marca is_loaded e
                    # regrava o status da linha confirmada: isso reaproveita
                    # o mesmo aviso de "inclusão" que quotation.line.write()
                    # (cotacoes) já dispara sozinho sempre que uma linha
                    # vira confirmed com a cotação já carregada — não
                    # precisa (nem deve) montar mensagem nova.
                    if (
                        confirmed_now
                        and target_quotation._is_past_notify_loaded_cutoff()
                        and not target_quotation.is_loaded
                    ):
                        target_quotation.set_is_loaded()
                        confirmed_line.write({'status': 'confirmed'})

        channel = self.env['mail.channel'].browse(message.res_id)
        if channel.exists() and channel.attendance_type == 'bot':
            channel.delete_password_queue()
            channel._notify_chatbot_clear_session(channel.id)

        return result
