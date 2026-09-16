from odoo import fields, models, api
import logging
import re
import datetime
import requests
import threading

_logger = logging.getLogger(__name__)


class Channel(models.Model):
    _inherit = 'mail.channel'

    attendance_type = fields.Selection(
        selection=[('bot', 'Bot'), ('human', 'Humano')],
        string='Tipo de atendimento',
        index=True
    )

    def _dispatch_to_ai_agent(self, message):
        """
        Envia a mensagem do cliente para o agente IA em uma thread separada
        para não bloquear o fluxo do Odoo. A resposta é enviada de volta via WhatsApp.
        """
        if self.env.context.get('is_button'):
            # CONFIRMAR/DESISTIR já são tratados inteiramente em
            # item_verification_response (fecha a fila e limpa a sessão do bot) —
            # essa checagem precisa vir ANTES do bloco abaixo: como esse método
            # também roda pra esses botões (mesmo ciclo em que a fila acabou de
            # ser fechada), sem isso o "if not self.attendance_type" reabriria o
            # atendimento e postaria "Atendimento iniciado pelo Bot" de novo.
            # Só PEÇA ERRADA segue pro agente, que dispara o fluxo de alterar termos.
            # message.body vem com tags HTML (ex.: "<span>PEÇA ERRADA</span>"),
            # por isso precisa remover antes de comparar.
            button_text = re.sub(r'<[^>]+>', '', message.body or '').strip().upper()
            if button_text != 'PEÇA ERRADA':
                return

        if not self.attendance_type:
            bot_user = self.env.ref('mail_gateway_whatsapp_bot.superglassbot_user', raise_if_not_found=False)
            self.write({'attendance_type': 'bot', 'seller_id': bot_user.id if bot_user else False})
            if self.queue_id:
                self.queue_id.sudo().write({'attendance_type': 'bot', 'seller_id': bot_user.id if bot_user else False})
            self.message_post(
                body='<b>Atendimento iniciado pelo Bot</b>',
                message_type='notification',
                subtype_xmlid='mail.mt_note',
            )

        agent_url = self.env['ir.config_parameter'].sudo().get_param(
            'cotacoes.ai_agent_url', 'http://localhost:8000'
        )
        agent_secret = self.env['ir.config_parameter'].sudo().get_param(
            'cotacoes.ai_agent_secret', ''
        )

        body_text = re.sub(r'<[^>]+>', '', message.body or '')
        body_text = body_text.replace('&nbsp;', ' ').replace('&amp;', '&').strip()

        # Imagens vão em base64 no payload pra o chatbot poder de fato "ver"
        # (leitura de modelo/placa/código gravado no vidro) — antes só o
        # marcador [ANEXO] chegava, sem o conteúdo. Só mimetype image/*;
        # áudio/documento continuam só com o marcador (has_attachment). Uma
        # falha lendo um anexo (filestore ausente, corrompido etc.) NUNCA
        # pode derrubar o dispatch da mensagem — antes desse recurso o fluxo
        # nem tocava no conteúdo do anexo, então isso preserva o
        # comportamento anterior mesmo se a leitura falhar: perde só a
        # imagem daquele anexo, a mensagem segue normalmente com [ANEXO].
        image_attachments = []
        for att in message.attachment_ids:
            if not att.mimetype or not att.mimetype.startswith('image/'):
                continue
            try:
                data = att.datas
                if isinstance(data, bytes):
                    data = data.decode('ascii')
            except Exception:
                _logger.exception(
                    'Falha ao ler anexo de imagem %s (channel=%s) — seguindo sem ele.',
                    att.id, self.id,
                )
                continue
            image_attachments.append({
                'mimetype': att.mimetype,
                'data': data,
                'name': att.name,
            })

        partner = message.author_id
        payload = {
            'channel_id': self.id,
            'message': body_text,
            'partner_id': partner.id if partner else None,
            'author_name': partner.name if partner else None,
            'is_button': bool(self.env.context.get('is_button')),
            # Foto/áudio/arquivo sem texto: o chatbot troca a mensagem vazia pelo
            # marcador [ANEXO] e pede os dados em texto, em vez de descartar.
            'has_attachment': bool(message.attachment_ids),
            'image_attachments': image_attachments,
        }

        channel_id = self.id

        def _call_agent():
            try:
                headers = {'Content-Type': 'application/json'}
                if agent_secret:
                    headers['X-Webhook-Secret'] = agent_secret

                resp = requests.post(
                    f'{agent_url}/webhook/whatsapp',
                    json=payload,
                    headers=headers,
                    # Payload com imagem em base64 é maior e mais lento que
                    # texto puro — 10s bastava antes, agora pode estourar.
                    timeout=30,
                )

                if resp.status_code != 200:
                    _logger.warning(
                        'AI agent Webhook returned error status=%s for channel=%s: %s',
                        resp.status_code, channel_id, resp.text
                    )
            except Exception:
                _logger.exception('Erro ao chamar Webhook da IA para channel=%s', channel_id)

        thread = threading.Thread(target=_call_agent, daemon=True)
        thread.start()

    def _resolve_notify_members(self, members):
        """Filters and pins channel members based on bot/human attendance type."""
        if self.attendance_type != 'human':
            admin_members = self.channel_member_ids.filtered(
                lambda lm: lm.partner_id in self.get_admin_user_partner().mapped('partner_id')
            )
            (members - admin_members).write({'is_pinned': False})
            return admin_members
        return super()._resolve_notify_members(members)

    def channel_info(self):
        channel_infos = super().channel_info()
        for c in channel_infos:
            if c.get('channel', {}).get('channel_type') == 'gateway':
                channel_record = self.browse(c['id'])
                c['attendance_type'] = channel_record.attendance_type or False
        return channel_infos

    @api.model
    def _cron_close_idle_bot_attendances(self):
        """
        Encerra atendimentos bot que estão ociosos há mais de N minutos.
        Considera ocioso quando a última mensagem do BOT no canal foi enviada
        há mais tempo que o prazo configurado.

        Exceção: se a última mensagem do bot for a de verificação com a
        logística (produto sem estoque, aguardando confirmação do time de
        estoque), o prazo de ociosidade não é respeitado — quem precisa
        responder é a logística, não o cliente.
        """
        idle_minutes = int(self.env['ir.config_parameter'].sudo().get_param(
            'cotacoes.bot_idle_timeout_minutes', '5'
        ))
        cutoff = datetime.datetime.now() - datetime.timedelta(minutes=idle_minutes)

        channels = self.search([
            ('attendance_type', '=', 'bot'),
            ('queue_id', '!=', False),
            ('channel_type', '=', 'gateway'),
        ])

        bot_user = self.env.ref('mail_gateway_whatsapp_bot.superglassbot_user', raise_if_not_found=False)
        bot_partner_id = bot_user.partner_id.id if bot_user else False

        for channel in channels:
            last_message = self.env['mail.message'].search(
                [
                    ('res_id', '=', channel.id),
                    ('model', '=', 'mail.channel'),
                    ('message_type', '=', 'comment'),
                    ('author_id', '=', bot_partner_id),
                ],
                order='date desc',
                limit=1,
            )
            if not last_message:
                continue
            if 'verificando a disponibilidade imediata com nossa logística' in (last_message.body or ''):
                continue

            if last_message.date <= cutoff:
                _logger.info(
                    'Encerrando atendimento bot ocioso: channel=%s, última msg=%s',
                    channel.id, last_message.date,
                )
                channel.delete_password_queue()
                self._notify_chatbot_clear_session(channel.id)

    def _notify_chatbot_clear_session(self, channel_id):
        agent_url = self.env['ir.config_parameter'].sudo().get_param('cotacoes.ai_agent_url', '')
        agent_secret = self.env['ir.config_parameter'].sudo().get_param('cotacoes.ai_agent_secret', '')
        if not agent_url:
            return
        try:
            headers = {'Content-Type': 'application/json'}
            if agent_secret:
                headers['X-Webhook-Secret'] = agent_secret
            requests.post(
                f'{agent_url}/internal/clear-session',
                json={'channel_id': channel_id},
                headers=headers,
                timeout=5,
            )
        except Exception as e:
            _logger.warning('Erro ao notificar chatbot para limpar sessão channel=%s: %s', channel_id, e)

    def delete_password_queue(self):
        was_bot = self.attendance_type == 'bot'
        self.write({'attendance_type': False})
        if was_bot:
            bot_user = self.env.ref('mail_gateway_whatsapp_bot.superglassbot_user', raise_if_not_found=False)
            if bot_user:
                return super(Channel, self.with_user(bot_user)).delete_password_queue()
        return super().delete_password_queue()
