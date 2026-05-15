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
        if not self.attendance_type:
            bot_user = self.env.ref('mail_gateway_whatsapp_bot.superglassbot_user', raise_if_not_found=False)
            self.write({'attendance_type': 'bot', 'seller_id': bot_user.id if bot_user else False})
            if self.queue_id:
                self.queue_id.sudo().write({'attendance_type': 'bot', 'seller_id': bot_user.id if bot_user else False})

        agent_url = self.env['ir.config_parameter'].sudo().get_param(
            'cotacoes.ai_agent_url', 'http://localhost:8080'
        )
        agent_secret = self.env['ir.config_parameter'].sudo().get_param(
            'cotacoes.ai_agent_secret', ''
        )

        body_text = re.sub(r'<[^>]+>', '', message.body or '')
        body_text = body_text.replace('&nbsp;', ' ').replace('&amp;', '&').strip()

        partner = message.author_id
        payload = {
            'channel_id': self.id,
            'message': body_text,
            'partner_id': partner.id if partner else None,
            'author_name': partner.name if partner else None,
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
                    timeout=10,
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
        Considera ocioso quando a última mensagem foi enviada pelo OdooBot
        e o cliente não respondeu dentro do prazo.
        """
        idle_minutes = int(self.env['ir.config_parameter'].sudo().get_param(
            'cotacoes.bot_idle_timeout_minutes', '5'
        ))
        cutoff = datetime.datetime.now() - datetime.timedelta(minutes=idle_minutes)
        odoobot_partner_id = self.env.ref('base.partner_root').id

        channels = self.search([
            ('attendance_type', '=', 'bot'),
            ('queue_id', '!=', False),
            ('channel_type', '=', 'gateway'),
        ])

        for channel in channels:
            last_message = self.env['mail.message'].search(
                [
                    ('res_id', '=', channel.id),
                    ('model', '=', 'mail.channel'),
                    ('message_type', '=', 'comment'),
                ],
                order='date desc',
                limit=1,
            )
            if last_message and last_message.author_id.id == odoobot_partner_id and last_message.date <= cutoff:
                _logger.info(
                    'Encerrando atendimento bot ocioso: channel=%s, última msg=%s',
                    channel.id, last_message.date,
                )
                channel.delete_password_queue()

    def delete_password_queue(self):
        self.write({'attendance_type': False})
        return super().delete_password_queue()

    def transfer_to_human(self, reason='', summary=''):
        """
        Transfere o atendimento do bot para um vendedor humano.
        Atualiza attendance_type, pina o canal para os vendedores da rota
        e envia bus para que o canal apareça no sidebar em tempo real.
        """
        self.ensure_one()

        self.write({'attendance_type': 'human', 'seller_id': False})
        if self.queue_id:
            msgs = self._channel_fetch_message()
            last_msg_id = msgs[0].get('id') if msgs else False
            bot_queue = self.queue_id
            bot_queue.sudo().write({
                'end_date': datetime.datetime.now(),
                'end_message_id': last_msg_id,
                'bot_escalated': True,
            })
            bot_queue.sudo().calculate_response_times()
            new_queue = self.env['quotation.queue'].sudo().create({
                'channel_id': self.id,
                'partner_id': bot_queue.partner_id.id,
                'initial_date': datetime.datetime.now(),
                'start_message_id': last_msg_id,
                'attendance_type': 'human',
            })
            bot_queue.sudo().write({'next_queue_id': new_queue.id})
            self.write({'queue_id': new_queue.id})

        members_to_pin = self.set_members_to_pin()
        members_to_pin.write({'is_pinned': True})

        for member in members_to_pin:
            self.env['bus.bus']._sendone(
                member.partner_id,
                'mail.channel/pin_unknowing_thread',
                {
                    'id': self.id,
                    'isServerPinned': True,
                    'last_interest_dt': fields.Datetime.now(),
                    'attendance_type': 'human',
                }
            )

        if reason or summary:
            body = "<b>Transferido para atendimento humano</b>"
            if reason:
                body += f"<br/><b>Motivo:</b> {reason}"
            if summary:
                body += f"<br/><b>Resumo:</b> {summary}"
            self.message_post(
                body=body,
                message_type="notification",
                subtype_xmlid="mail.mt_note",
            )

        return True
