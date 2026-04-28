from odoo import models


class MailGatewayWhatsapp(models.AbstractModel):
    _inherit = 'mail.gateway.whatsapp'

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
                        if message.get("type") == "button":
                            self._process_button(message.get("button", {}).get("payload"), message)
                        elif message_id:
                            self._on_message_received_bot(chat, message_id, queue_created)

    def _on_message_received_bot(self, chat, message_id, queue_created):
        if chat.attendance_type != 'human':
            self._apply_whitelist_rule(chat, message_id)
        if queue_created and chat.attendance_type == 'human':
            self._send_attendance_start(mobile=chat.gateway_channel_token)

    def _apply_whitelist_rule(self, channel, message_id):
        rules = self.env['mail.gateway.whitelist'].search([
            ('gateway_id', '=', channel.gateway_id.id),
            ('code', '!=', False),
        ])
        if not rules:
            channel.write({'attendance_type': 'human'})
            return

        partner = self.env['res.partner.gateway.channel'].search(
            [('gateway_token', '=', channel.gateway_channel_token)],
            limit=1,
        ).partner_id

        for rule in rules:
            if partner in rule.partner_ids:
                rule.execute(channel, message_id)
                return

        channel.write({'attendance_type': 'human'})
