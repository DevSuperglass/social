from odoo import models


class MailGatewayWhatsapp(models.AbstractModel):
    _inherit = 'mail.gateway.whatsapp'

    def create_message(self, mobile, body_message, gateway_id):
        """
        create_message() (mail_gateway_whatsapp) atribui author_id=2 (OdooBot)
        sempre que a mensagem é enviada com is_internal=True ou por um cron
        (todos rodam como base.user_root, cujo partner também é OdooBot) —
        nunca como SuperglassBot. O cron _cron_close_idle_bot_attendances só
        reconhece como "última mensagem do bot" as que têm author_id do
        SuperglassBot; mensagens do OdooBot ficam invisíveis pra ele e o
        atendimento pode ser encerrado por ociosidade mesmo tendo acabado de
        receber uma mensagem automática.

        A correção precisa acontecer ANTES do super().create_message(), não
        depois: o message_post do canal (Channel.message_post, em cotacoes)
        já dispara em tempo real a réplica da mensagem na cotação
        (_link_message_post → model_message_post) copiando o author_id de
        quem acabou de ser criado — tudo isso ainda DENTRO da chamada ao
        método original. Corrigir só depois (message.author_id = ...) chega
        tarde: a réplica na cotação já nasceu com o autor errado e um write
        posterior não a alcança retroativamente. Por isso resolve o canal
        antes, e se o atendimento for do bot, roda o create_message original
        já como SuperglassBot (with_user) e sem is_internal (que força
        author_id=2 direto), pra a mensagem nascer certa desde o início.
        """
        channel = self._get_channel(gateway_id, mobile, {}, force_create=False)
        caller = self
        if channel and channel.attendance_type == 'bot':
            bot_user = self.env.ref('mail_gateway_whatsapp_bot.superglassbot_user', raise_if_not_found=False)
            if bot_user:
                caller = self.with_user(bot_user).with_context(is_internal=False)

        return super(MailGatewayWhatsapp, caller).create_message(mobile, body_message, gateway_id)

    def _receive_update(self, gateway, update):
        if update:
            for entry in update["entry"]:
                for change in entry["changes"]:
                    if change["field"] != "messages":
                        continue
                    for message in change["value"].get("messages", []):
                        chat = self._get_channel(
                            gateway, message["from"], change["value"], force_create=True
                        )
                        if not chat:
                            continue
                        message_id = self._process_update(chat, message, change["value"])
                        queue_created = False
                        if message_id:
                            queue_created = self._set_queue(chat, message_id)
                        is_button = message.get("type") == "button"
                        if is_button:
                            self._process_button(message.get("button", {}).get("payload"), message)
                        if message_id:
                            self._on_message_received_bot(chat, message_id, queue_created, is_button=is_button)

    def _on_message_received_bot(self, chat, message_id, queue_created, is_button=False):
        if chat.attendance_type != 'human':
            self._apply_whitelist_rule(chat, message_id, is_button=is_button)
        if queue_created and chat.attendance_type == 'human':
            self._send_attendance_start(mobile=chat.gateway_channel_token)

    def _apply_whitelist_rule(self, channel, message_id, is_button=False):
        rules = self.env['mail.gateway.whitelist'].search([
            ('gateway_id', '=', channel.gateway_id.id),
            ('code', '!=', False),
        ])
        if not rules:
            self._set_human_attendance(channel)
            return

        partner = self.env['res.partner.gateway.channel'].search(
            [('gateway_token', '=', channel.gateway_channel_token)],
            limit=1,
        ).partner_id

        for rule in rules:
            if partner in rule.partner_ids:
                rule.execute(channel.with_context(is_button=is_button), message_id)
                return

        self._set_human_attendance(channel)

    def _set_human_attendance(self, channel):
        """Marca canal (e a queue vinculada, se houver) como atendimento humano."""
        channel.write({'attendance_type': 'human'})
        if channel.queue_id:
            channel.queue_id.sudo().write({'attendance_type': 'human'})
