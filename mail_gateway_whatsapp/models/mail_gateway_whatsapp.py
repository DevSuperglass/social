# Copyright 2024 Dixmit
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import base64
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
from odoo.exceptions import UserError, ValidationError
from odoo.http import request
from odoo.tools import html2plaintext

from odoo.addons.base.models.ir_mail_server import MailDeliveryException

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
            hashlib.sha256, ).hexdigest()
            != signature
        ):
            return False
        return True

    def _get_channel_vals(self, gateway, token, update):
        result = super()._get_channel_vals(gateway, token, update)
        for contact in update.get("contacts", []):
            if contact["wa_id"] == token:
                result["name"] = contact["profile"]["name"]
                continue
        return result

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
                        if message_id:
                            self._set_queue(chat, message_id)
                        # self._get_crm_meta(message.get("from"))
                        if message.get("type") == "button":
                            self._process_button(message.get("button", {}).get("payload"), message)

    @staticmethod
    def convert_audio(content):
        ogg_audio = AudioSegment.from_file(BytesIO(content), format="ogg")

        mp3_io = BytesIO()
        ogg_audio.export(mp3_io, format="mp3")

        converted_content = mp3_io.getvalue()

        return converted_content

    def _transcribe_audio(self, audio_bytes: bytes) -> str:
        """Transcreve áudio usando o provedor configurado em Configurações → WhatsApp."""
        get_param = self.env['ir.config_parameter'].sudo().get_param
        provider = get_param('mail_gateway_whatsapp.transcription_provider', '')
        if not provider:
            return ''
        if provider == 'groq':
            return self._transcribe_groq(audio_bytes, get_param('mail_gateway_whatsapp.groq_api_key', ''))
        if provider == 'deepgram':
            return self._transcribe_deepgram(audio_bytes, get_param('mail_gateway_whatsapp.deepgram_api_key', ''))
        if provider == 'assemblyai':
            return self._transcribe_assemblyai(audio_bytes, get_param('mail_gateway_whatsapp.assemblyai_api_key', ''))
        _logger.warning('Provedor de transcrição desconhecido: %s', provider)
        return ''

    def _transcribe_groq(self, audio_bytes: bytes, api_key: str) -> str:
        """Transcrição via Groq (whisper-large-v3)."""
        if not api_key:
            return ''
        try:
            from io import BytesIO as _BytesIO
            resp = requests.post(
                'https://api.groq.com/openai/v1/audio/transcriptions',
                headers={'Authorization': f'Bearer {api_key}'},
                files={'file': ('audio.mp3', _BytesIO(audio_bytes), 'audio/mpeg')},
                data={'model': 'whisper-large-v3', 'language': 'pt', 'response_format': 'text'},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.text.strip()
        except Exception:
            _logger.exception('Erro ao transcrever áudio via Groq')
            return ''

    def _transcribe_deepgram(self, audio_bytes: bytes, api_key: str) -> str:
        """Transcrição via Deepgram (nova-2, pt-BR)."""
        if not api_key:
            return ''
        try:
            resp = requests.post(
                'https://api.deepgram.com/v1/listen',
                params={'model': 'nova-2', 'language': 'pt-BR'},
                headers={'Authorization': f'Token {api_key}', 'Content-Type': 'audio/mpeg'},
                data=audio_bytes,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()['results']['channels'][0]['alternatives'][0]['transcript'].strip()
        except Exception:
            _logger.exception('Erro ao transcrever áudio via Deepgram')
            return ''

    def _transcribe_assemblyai(self, audio_bytes: bytes, api_key: str) -> str:
        """Transcrição via AssemblyAI (universal-2, pt)."""
        import time as _time
        if not api_key:
            return ''
        headers = {'authorization': api_key}
        try:
            upload = requests.post(
                'https://api.assemblyai.com/v2/upload',
                headers={**headers, 'content-type': 'application/octet-stream'},
                data=audio_bytes,
                timeout=30,
            )
            upload.raise_for_status()
            audio_url = upload.json()['upload_url']

            transcript = requests.post(
                'https://api.assemblyai.com/v2/transcript',
                headers=headers,
                json={'audio_url': audio_url, 'language_code': 'pt', 'language_detection': False, 'speech_models': ['universal-2']},
                timeout=15,
            )
            if not transcript.ok:
                _logger.error('AssemblyAI /transcript status=%s body=%s', transcript.status_code, transcript.text)
            transcript.raise_for_status()
            transcript_id = transcript.json()['id']

            for _ in range(60):
                _time.sleep(1)
                poll = requests.get(
                    f'https://api.assemblyai.com/v2/transcript/{transcript_id}',
                    headers=headers,
                    timeout=10,
                )
                poll.raise_for_status()
                data = poll.json()
                if data['status'] == 'completed':
                    return (data.get('text') or '').strip()
                if data['status'] == 'error':
                    _logger.error('AssemblyAI erro: %s', data.get('error'))
                    return ''
            _logger.warning('AssemblyAI timeout ao aguardar transcrição')
            return ''
        except Exception:
            _logger.exception('Erro ao transcrever áudio via AssemblyAI')
            return ''

    def base64_decode(self, base64_string):
        pattern = re.compile(b'^\x1c\x18(?:\x0c|\r)(\d+)\x15\x02\x00(.)\x18(.)([A-Z0-9]+)\x00$')
        base64_string = base64_string[6:]
        if not base64_string:
            return None
        if len(base64_string) % 4 != 0:
            _logger.warning("Tamanho do id em base64 não é divisivel por 4: %s", base64_string)
        try:
            decoded_bytes = base64.b64decode(base64_string)
            match = pattern.search(decoded_bytes)
            if match:
                return match.group(4).decode("UTF8")
        except Exception as e:
            _logger.error("Erro ao decodificar base64 no recebimento: %s", e)
            return None

    def _process_update(self, chat, message, value):
        chat.ensure_one()
        body = ""
        attachments = []
        transcription = ''
        if message.get("text"):
            body = message.get("text").get("body")
        if message.get("payload_text"):
            body = message['payload_text']
        if message.get("type") == 'button':
            body = message.get('button').get('text')
        for key in ["image", "audio", "video", "document", "sticker"]:
            if message.get(key):
                attachment_id = message.get(key).get("id")
                if attachment_id:
                    body = message.get(key).get("caption") or ""
                    info_requests = requests.get(
                        "https://graph.facebook.com/v%s/%s"
                        % (
                            chat.gateway_id.whatsapp_version,
                            attachment_id,
                        ),
                        headers={
                            "Authorization": "Bearer %s" % chat.gateway_id.token,
                        },
                        timeout=10,
                        proxies=self._get_proxies(),
                    )
                    info_requests.raise_for_status()
                    attachment_json = info_requests.json()
                    attachment_url = attachment_json["url"]
                else:
                    attachment_url = message.get(key).get("url")
                if not attachment_url:
                    continue
                attachment_request = requests.get(
                    attachment_url,
                    headers={
                        "Authorization": "Bearer %s" % chat.gateway_id.token,
                    },
                    timeout=10,
                    proxies=self._get_proxies(),
                )
                attachment_request.raise_for_status()

                converted_audio = None

                if key == 'audio':
                    attachment_json['mime_type'] = 'audio/mpeg'
                    converted_audio = self.convert_audio(content=attachment_request.content)
                    transcription = self._transcribe_audio(converted_audio)
                    if transcription:
                        body = transcription

                attachments.append(
                    (
                        "{}{}".format(
                            attachment_id,
                            mimetypes.guess_extension(attachment_json["mime_type"]),
                        ),
                        attachment_request.content if key != 'audio' else converted_audio,
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
        if body or attachments:
            author = self._get_author(chat.gateway_id, value)
            if not chat.route_id and author.route_id:
                chat.write({'route_id': author.route_id.id})
            new_message = chat.with_context({'no_gateway_notification': True}).message_post(
                body=body,
                author_id=author and author._name == "res.partner" and author.id,
                gateway_type="whatsapp",
                date=datetime.fromtimestamp(int(message["timestamp"])),
                subtype_xmlid="mail.mt_comment",
                message_type="comment",
                attachments=attachments,
                parent_id=self._get_parent_message(message),
                whatsapp_decoded_id=self.base64_decode(message.get("id")),
                whatsapp_id=message.get("id")
            )
            self._post_process_message(new_message, chat)
            return new_message
        else:
            _logger.warning("JSON DA MENSAGEM VAZIA: " + str(message))
            return None

    def _set_queue(self, channel_id, message_id):
        """
            Criação de atendimento.
            Retorna True se uma nova fila foi criada, False caso contrário.
        """

        if not channel_id.queue_id and channel_id.gateway_id.whatsapp_from_phone == '335789752960181':
            partner_id = self.env['res.partner.gateway.channel'].search(
                [
                    ('gateway_token', '=', channel_id.gateway_channel_token)
                ],
                limit=1
            ).partner_id
            bot_user = self.env['res.users'].sudo().search([('login', '=', 'superglassbot')], limit=1)
            channel_id.write({
                'queue_id': self.env['quotation.queue'].sudo().create({
                    'channel_id': channel_id.id,
                    'partner_id': partner_id.id,
                    'seller_id': bot_user.id if bot_user else False,
                    'initial_date': datetime.now(),
                    'start_message_id': message_id.id,
                    'quotation_id': message_id.gateway_message_id.res_id
                    if message_id.gateway_message_id.model == 'quotation' else False
                }).id,
                'queue_priority': int(partner_id.priority_rating)
            })
            channel_id.message_post(
                body='<b>Atendimento iniciado pelo Bot</b>',
                message_type='notification',
                subtype_xmlid='mail.mt_note',
            )
            return True
        return False

    def _send_attendance_start(self, mobile):
        self.with_context({'is_internal': True}).send_tmpl_message(
            tmpl_name=None,
            gateway_phone='335789752960181',
            components="Seu atendimento será iniciado em breve",
            mobile_list=[mobile],
            body_message="Seu atendimento será iniciado em breve"
        )

    def _get_crm_meta(self, number):
        change_status = self.env['crm.lead'].sudo().search([('mobile', '=', number), ('new_status', '=', 'draft')])

        if change_status:
            change_status.new_status = 'in_progress'
            change_status.remove_button = True

    def _process_button(self, button_template, message):
        parent_id = self._get_parent_message(message)

        if not parent_id:
            _logger.warning(
                f"Mensagem {message} não possui pai."
            )
            return

        if button_template:
            waid_record = request.env['whatsapp.template.waid'].sudo().search(
                [
                    ('mail_message_id', '=', parent_id)
                ]
            )

            button_record = request.env['whatsapp.template.button'].sudo().search(
                [
                    ('name', '=', button_template),
                    ('whatsapp_template_id', '=', waid_record.whatsapp_template_id.id)
                ]
            )

            if button_record.code:
                model = button_record.env[button_record.model_id.model].with_context(
                    button=button_template,
                    waid=message.get('context', {}).get('id')
                )
                function_to_call = getattr(model, button_record.code, None)
                if callable(function_to_call):
                    function_to_call()
                else:
                    _logger.warning(f"Função do botão não é executável para a mensagem {message}.")
            else:
                _logger.warning(f"Botão do template não encontrado para a mensagem {message}.")

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
            body = self._get_message_body(record)
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
                    gateway.whatsapp_from_phone
                )
                headers = {"Authorization": "Bearer %s" % gateway.token}
                json = self._send_payload(
                    record.gateway_channel_id,
                    media_id=response.json()["id"],
                    media_type=attachment_type,
                    media_name=attachment.name,
                    body=body,
                    message_id=record.mail_message_id
                )
                message = self._create_request_line(url=url, headers=headers, json=json, record=record)

            if body and not message:
                user_name = "*[{}]* ".format(self.env.user.name)
                body = user_name + body
                url = "https://graph.facebook.com/v%s/%s/messages" % (
                    gateway.whatsapp_version,
                    gateway.whatsapp_from_phone,
                )
                headers = {"Authorization": "Bearer %s" % gateway.token}
                json = self._send_payload(record.gateway_channel_id, body=body, message_id=record.mail_message_id)
                # if message:
                #     raise ValidationError(
                #         "Não é possível enviar descrição e mídia na mesma mensagem. \n"
                #         "Envie o conteúdo em mensagens separadas e referencie uma à outra para garantir a contextualização."
                #     )
                message = self._create_request_line(url=url, headers=headers, json=json, record=record)
        except Exception as exc:
            raise UserError(
                _("Erro ao enviar a mensagem pelo WhatsApp:\n%s") % str(exc)
            )

        if message:
            record.sudo().write(
                {
                    "notification_status": "sent",
                    "failure_reason": False,
                }
            )

        if auto_commit is True:
            # pylint: disable=invalid-commit
            self.env.cr.commit()

    def _create_request_line(self, url, headers, json, record):
        return self.env['whatsapp.request'].sudo().create({
            'url': url,
            'headers': headers,
            'json': json,
            'mail_message_id': record.mail_message_id.id
        })

    def _send_payload(
        self, channel, message_id, body=False, media_id=False, media_type=False, media_name=False
    ):
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": channel.gateway_channel_token,
        }

        context_data = {}

        if message_id.parent_id:
            context_data = {
                "context": {
                    "message_id": message_id.parent_id.whatsapp_id
                }
            }

        if media_id:
            media_data = {"id": media_id}
            if media_type == "document":
                media_data["filename"] = media_name
            payload.update({
                "type": media_type,
                media_type: media_data,
            })
            if body:
                user_name = "*[{}]* ".format(self.env.user.name)
                formated_body = user_name + html2plaintext(body)
                payload.get("image").update({"caption": formated_body})

        elif body:
            payload.update({
                "type": "text",
                "text": {"preview_url": False, "body": html2plaintext(body)},
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
                ],
                limit=1
            )
            if gateway_partner:
                return gateway_partner.partner_id
            partner = self._get_partner(update)
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

    def _get_partner(self, update):
        number = update.get("messages")[0].get("from")
        partner_id = self.env['res.partner'].search(
            [
                ('phone_sanitized', '=', "+" + number)
            ]
        )
        if not partner_id:
            vals_list = {
                'name': update['contacts'][0]['profile']['name'],
            }

            vals_list.update(
                {'phone': number, 'whatsapp_contact': 'phone'}
            ) if len(number) == 12 else vals_list.update(
                {'mobile': number, 'whatsapp_contact': 'mobile'}
            )
            partner_id = self.env['res.partner'].create(vals_list)
        return partner_id

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

    def send_tmpl_message(self, gateway_phone, tmpl_name, components, mobile_list, body_message):
        gateway = self.env['mail.gateway'].search([('whatsapp_from_phone', '=', gateway_phone)], limit=1)
        tmpl_id = self.env['whatsapp.template'].search([('name', '=', tmpl_name)], limit=1)

        for mobile in mobile_list:
            message = self.create_message(mobile, body_message, gateway)
            if not message:
                raise UserError(
                    f'O número de telefone {mobile} não é válido. Para realizar o envio, utilize o seguinte formato: 55DDD(9)Telefone. Exemplo: 5511912345678.')

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
                                 'components': components
                             }})
                self.env['whatsapp.template.waid'].sudo().create({
                    'whatsapp_template_id': tmpl_id.id,
                    'body': body_message,
                    'mail_message_id': message.id
                })
            else:
                json.update({"type": 'text',
                             "text": {
                                 "body": components
                             }})

            self.env['whatsapp.request'].sudo().create({
                'url': f'https://graph.facebook.com/v{gateway.whatsapp_version}/{gateway.whatsapp_from_phone}/messages',
                'headers': {
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {gateway.token}'
                },
                'json': json,
                'mail_message_id': message.id,
            })
        return True

    def create_message(self, mobile, body_message, gateway_id):
        update = {
            'messages': [{'from': mobile}],
            'contacts': [{'wa_id': mobile, 'profile': {'name': mobile}}]
        }
        channel = self._get_channel(gateway_id, mobile, update, force_create=True)

        if channel:
            message = channel.with_context({
                'no_gateway_notification': True,
                'no_auto_pin': not channel.queue_id
            }).message_post(
                body=body_message,
                author_id=2 if self.env.context.get('is_internal') else self.env['res.users'].browse(
                    self.env.uid).partner_id.id,
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
                gateway_type="whatsapp",
                date=datetime.today(),
            )
            self._post_process_message(message, channel)
            return message
        return None
