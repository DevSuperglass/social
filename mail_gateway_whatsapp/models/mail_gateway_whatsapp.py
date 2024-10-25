# Copyright 2024 Dixmit
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import hashlib
import hmac
import logging
import mimetypes
import traceback
from datetime import datetime
from io import StringIO
import re

import requests
import requests_toolbelt

from odoo import _, models
from odoo.exceptions import UserError
from odoo.http import request
from odoo.tools import html2plaintext

from odoo.addons.base.models.ir_mail_server import MailDeliveryException

# PROPRIETÁRIO
import base64
from io import BytesIO
from pydub import AudioSegment

_logger = logging.getLogger(__name__)


class MailGatewayWhatsappService(models.AbstractModel):
    _inherit = "mail.gateway.abstract"
    _name = "mail.gateway.whatsapp"
    _description = "Whatsapp Gateway services"

    def _receive_get_update(self, bot_data, req, **kwargs):
        self._verify_update(bot_data, {})
        gateway = self.env["mail.gateway"].browse(bot_data["id"])
        if kwargs.get("hub.verify_token") != gateway.whatsapp_security_key:
            return None
        gateway.sudo().integrated_webhook_state = "integrated"
        response = request.make_response(kwargs.get("hub.challenge"))
        response.status_code = 200
        return response

    def _set_webhook(self, gateway):
        gateway.integrated_webhook_state = "pending"

    def _verify_update(self, bot_data, kwargs):
        signature = request.httprequest.headers.get("x-hub-signature-256")
        if not signature:
            return False
        if (
            "sha256=%s"
            % hmac.new(
            bot_data["webhook_secret"].encode(),
            request.httprequest.data,
            hashlib.sha256,
        ).hexdigest()
            != signature
        ):
            return True
        return True

    def _get_channel_vals(self, gateway, token, update):
        result = super()._get_channel_vals(gateway, token, update)
        for contact in update.get("contacts", []):
            if contact["wa_id"] == token:
                result["name"] = contact["profile"]["name"]
                continue
        return result

    def _receive_update(self, gateway, update, whats_id, reply_id, from_webhook):
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
                        message_type = message.get("type")
                        if message_type == "button":
                            button_payload = message.get("button", {}).get("payload")
                            if button_payload:
                                text_from_payload = button_payload
                                message['payload_text'] = text_from_payload
                        self._process_update(chat, message, change["value"], whats_id, reply_id, from_webhook)

    @staticmethod
    def convert_audio(content):
        ogg_audio = AudioSegment.from_file(BytesIO(content), format="ogg")

        mp3_io = BytesIO()
        ogg_audio.export(mp3_io, format="mp3")

        converted_content = mp3_io.getvalue()

        return converted_content

    def _process_update(self, chat, message, value, whats_id, reply_id, from_webhook):
        chat.ensure_one()
        body = ""
        attachments = []
        if message.get("text"):
            body = message.get("text").get("body")
        if message.get("payload_text"):
            body = message['payload_text']
        for key in ["image", "audio", "video", "document", "sticker"]:
            if message.get(key):
                image_id = message.get(key).get("id")
                if image_id:
                    image_info_request = requests.get(
                        "https://graph.facebook.com/v%s/%s"
                        % (
                            chat.gateway_id.whatsapp_version,
                            image_id,
                        ),
                        headers={
                            "Authorization": "Bearer %s" % chat.gateway_id.token,
                        },
                        timeout=10,
                        proxies=self._get_proxies(),
                    )
                    image_info_request.raise_for_status()
                    image_info = image_info_request.json()
                    image_url = image_info["url"]
                else:
                    image_url = message.get(key).get("url")
                if not image_url:
                    continue
                image_request = requests.get(
                    image_url,
                    headers={
                        "Authorization": "Bearer %s" % chat.gateway_id.token,
                    },
                    timeout=10,
                    proxies=self._get_proxies(),
                )
                image_request.raise_for_status()

                converted_audio = None

                if key == 'audio':
                    image_info['mime_type'] = 'audio/mpeg'
                    converted_audio = self.convert_audio(content=image_request.content)

                attachments.append(
                    (
                        "{}{}".format(
                            image_id,
                            mimetypes.guess_extension(image_info["mime_type"]),
                        ),
                        image_request.content if key != 'audio' else converted_audio,
                    )
                )
        if message.get("location"):
            body += (
                '<a target="_blank" href="https://www.google.com/'
                'maps/search/?api=1&query=%s,%s">Location</a>'
                % (
                    message["location"]["latitude"],
                    message["location"]["longitude"],
                )
            )
        if message.get("contacts"):
            pass
        if len(body) > 0 or attachments:
            author = self._get_author(chat.gateway_id, value)
            new_message = chat.message_post(
                body=body,
                author_id=author and author._name == "res.partner" and author.id,
                gateway_type="whatsapp",
                date=datetime.fromtimestamp(int(message["timestamp"])),
                # message_id=update.message.message_id,
                subtype_xmlid="mail.mt_comment",
                message_type="comment",
                attachments=attachments,
                parent_id=reply_id,
                whatsapp_id=whats_id,
                from_webhook=from_webhook
            )
            self._post_process_message(new_message, chat)
            related_message_id = message.get("context", {}).get("id", False)
            if related_message_id:
                related_message = (
                    self.env["mail.notification"]
                    .search(
                        [
                            ("gateway_channel_id", "=", chat.id),
                            ("gateway_message_id", "=", related_message_id),
                        ]
                    )
                    .mail_message_id
                )
                if related_message and related_message.gateway_message_id:
                    new_related_message = (
                        self.env[related_message.gateway_message_id.model]
                        .browse(related_message.gateway_message_id.res_id)
                        .message_post(
                            body=body,
                            author_id=author
                                      and author._name == "res.partner"
                                      and author.id,
                            gateway_type="whatsapp",
                            date=datetime.fromtimestamp(int(message["timestamp"])),
                            # message_id=update.message.message_id,
                            subtype_xmlid="mail.mt_comment",
                            message_type="comment",
                            attachments=attachments,
                            parent_id=reply_id,
                            whatsapp_id=whats_id,
                            from_webhook=from_webhook
                        )
                    )
                    self._post_process_reply(related_message)
                    new_message.gateway_message_id = new_related_message

    def _send(
        self,
        gateway,
        record,
        auto_commit=False,
        raise_exception=False,
        parse_mode=False,
    ):
        message = False
        try:
            attachment_mimetype_map = self._get_whatsapp_mimetype_kind()
            for attachment in record.mail_message_id.attachment_ids:
                if attachment.mimetype not in attachment_mimetype_map:
                    raise UserError(_("Mimetype is not valid"))
                attachment_type = attachment_mimetype_map[attachment.mimetype]
                m = requests_toolbelt.multipart.encoder.MultipartEncoder(
                    fields={
                        "file": (
                            attachment.name,
                            attachment.raw,
                            attachment.mimetype,
                        ),
                        "messaging_product": "whatsapp",
                        # "type": attachment_type
                    },
                )

                response = requests.post(
                    "https://graph.facebook.com/v%s/%s/media"
                    % (
                        gateway.whatsapp_version,
                        gateway.whatsapp_from_phone,
                    ),
                    headers={
                        "Authorization": "Bearer %s" % gateway.token,
                        "content-type": m.content_type,
                    },
                    data=m,
                    timeout=10,
                    proxies=self._get_proxies(),
                )
                response.raise_for_status()

                url = "https://graph.facebook.com/v%s/%s/messages" % (
                    gateway.whatsapp_version,
                    gateway.whatsapp_from_phone)
                headers = {"Authorization": "Bearer %s" % gateway.token}
                json = self._send_payload(
                    record.gateway_channel_id,
                    media_id=response.json()["id"],
                    media_type=attachment_type,
                    media_name=attachment.name,
                )
                message = self._create_request_line(url=url, headers=headers, json=json, record=record)

            body = self._get_message_body(record)
            if body:
                user_name = "*[{}]* ".format(self.env.user.name)
                body = user_name + body
                url = "https://graph.facebook.com/v%s/%s/messages" % (
                    gateway.whatsapp_version,
                    gateway.whatsapp_from_phone,
                )
                headers = {"Authorization": "Bearer %s" % gateway.token}
                json = self._send_payload(record.gateway_channel_id, body=body)
                message = self._create_request_line(url=url, headers=headers, json=json, record=record)
        except Exception as exc:
            buff = StringIO()
            traceback.print_exc(file=buff)
            _logger.error(buff.getvalue())
            if raise_exception:
                raise MailDeliveryException(
                    _("Unable to send the whatsapp message")
                ) from exc
            else:
                _logger.warning(
                    "Issue sending message with id {}: {}".format(record.id, exc)
                )
                record.sudo().write(
                    {"notification_status": "exception", "failure_reason": exc}
                )
        if message:
            record.sudo().write(
                {
                    "notification_status": "sent",
                    "failure_reason": False,
                    # "gateway_message_id": message["messages"][0]["id"],
                }
            )

        if auto_commit is True:
            # pylint: disable=invalid-commit
            self.env.cr.commit()

    def _create_request_line(self, url, headers, json, record):
        return self.env['whatsapp.request'].sudo().create(
            {'url': url, 'headers': headers, 'json': json, 'mail_message_id': record.mail_message_id.id})

    def _send_payload(
        self, channel, body=False, media_id=False, media_type=False, media_name=False
    ):
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": channel.gateway_channel_token,
        }

        context_data = {}

        if body:
            formated_body = re.sub(r"\*.*\*", "", body).strip()
            last_message = self.env['mail.message'].search([
                ('model', '=', 'mail.channel'),
                ('res_id', '=', channel.id)
            ], order='create_date desc', limit=1)

            last_message_body = str(last_message.body)

            if last_message_body == formated_body:
                if last_message.parent_id:
                    context_data = {
                        "context": {
                            "message_id": last_message.parent_id.whatsapp_id
                        }
                    }
            else:
                message_body = self.env['mail.message'].search([
                    ('model', '=', 'mail.channel'),
                    ('res_id', '=', channel.id),
                    ('body', '=', formated_body)
                ], order='create_date desc', limit=1)
                if message_body.parent_id:
                    context_data = {
                        "context": {
                            "message_id": message_body.parent_id.whatsapp_id
                        }
                    }
            payload.update({
                "type": "text",
                "text": {"preview_url": False, "body": html2plaintext(body)},
            })

        if media_id:
            media_data = {"id": media_id}
            if media_type == "document":
                media_data["filename"] = media_name
            payload.update({
                "type": media_type,
                media_type: media_data,
            })

        if context_data:
            payload.update(context_data)

        return payload

    def _get_whatsapp_mimetype_kind(self):
        return {
            "text/plain": "document",
            "application/pdf": "document",
            "application/vnd.ms-powerpoint": "document",
            "application/msword": "document",
            "application/vnd.ms-excel": "document",
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document": "document",
            "application/vnd.openxmlformats-officedocument."
            "presentationml.presentation": "document",
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet": "document",
            "audio/aac": "audio",
            "audio/mp4": "audio",
            "audio/mpeg": "audio",
            "audio/amr": "audio",
            "audio/ogg": "audio",
            "image/jpeg": "image",
            "image/png": "image",
            "video/mp4": "video",
            "video/3gp": "video",
            "image/webp": "sticker",
        }

    def _get_author(self, gateway, update):
        author_id = update.get("messages")[0].get("from")
        if author_id:
            gateway_partner = self.env["res.partner.gateway.channel"].search(
                [
                    ("gateway_id", "=", gateway.id),
                    ("gateway_token", "=", str(author_id)),
                ]
            )
            if gateway_partner:
                return gateway_partner.partner_id
            partner = self.env["res.partner"].search(
                [("phone_sanitized", "=", "+" + str(author_id))], limit=1
            )
            if partner:
                self.env["res.partner.gateway.channel"].create(
                    {
                        "name": gateway.name,
                        "partner_id": partner.id,
                        "gateway_id": gateway.id,
                        "gateway_token": str(author_id),
                    }
                )
                return partner
            guest = self.env["mail.guest"].search(
                [
                    ("gateway_id", "=", gateway.id),
                    ("gateway_token", "=", str(author_id)),
                ]
            )
            if guest:
                return guest
            author_vals = self._get_author_vals(gateway, author_id, update)
            if author_vals:
                return self.env["mail.guest"].create(author_vals)

        return False

    def _get_author_vals(self, gateway, author_id, update):
        for contact in update.get("contacts", []):
            if contact["wa_id"] == author_id:
                return {
                    "name": contact.get("profile", {}).get("name", "Anonymous"),
                    "gateway_id": gateway.id,
                    "gateway_token": str(author_id),
                }

    def _get_proxies(self):
        # This hook has been created in order to add a proxy if needed.
        # By default, it does nothing.
        return {}

    def _send_tmpl_message(self, tmpl_name, body_values, mobile, body_message):
        gateway = self.env['mail.gateway'].search([('whatsapp_from_phone', '=', '413704291825593')], limit=1)
        tmpl_id = self.env['whatsapp.template'].search([('name', '=', tmpl_name)], limit=1)
        message = self.create_message(mobile, body_message)

        json = {
            'messaging_product': 'whatsapp',
            'recipient_type': 'individual',
            'to': mobile,
        }

        if tmpl_id:
            json.update({'type': 'template',
                         'template': {
                             'name': tmpl_id.template_name,
                             'language': {'code': tmpl_id.lang_code},
                             'components': [
                                 {
                                     "type": "body",
                                     "parameters": body_values
                                 }
                             ]
                         }})
            self.env['whatsapp.template.waid'].sudo().create({
                'whatsapp_template_id': tmpl_id.id,
                'body': body_message,
                'mail_message_id': message.id
            })
        else:
            json.update({"type": 'text',
                         "text": {
                             "body": body_values
                         }})

        self.env['whatsapp.request'].sudo().create({
            'url': f'https://graph.facebook.com/v20.0/{gateway.whatsapp_from_phone}/messages',
            'headers': {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {gateway.token}'
            },
            'json': json,
            'mail_message_id': message.id,
        })

    def create_message(self, mobile, body_message):
        channel = self.env['mail.channel'].search([
            ('gateway_channel_token', '=', mobile),
            ('channel_type', '=', 'gateway')
        ], limit=1)

        if channel:
            message = self.env['mail.message'].create({
                'body': body_message,
                'message_type': 'comment',
                'subtype_id': self.env.ref('mail.mt_comment').id,
                'model': 'mail.channel',
                'res_id': channel.id,
                'author_id': self.env.user.partner_id.id,
                'gateway_type': 'whatsapp',
            })

            self.env['mail.notification'].create({
                'mail_message_id': message.id,
                'notification_type': 'gateway',
                'notification_status': 'sent',
                'author_id': self.env.user.partner_id.id,
                'gateway_channel_id': channel.id,
                'gateway_type': 'whatsapp',
            })._set_read_gateway()

            return message
