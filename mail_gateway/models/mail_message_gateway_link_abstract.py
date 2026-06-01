# Copyright 2024 Dixmit
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class MailMessageGatewayLinkAbstract(models.AbstractModel):
    _name = "mail.message.gateway.link.abstract"
    _description = "Link message from gateway"

    message_id = fields.Many2one(
        comodel_name="mail.message"
    )

    resource_ref = fields.Reference(
        string="Record reference", selection="_selection_target_model"
    )

    @api.model
    def _selection_target_model(self):
        models = self.env["ir.model"].sudo().search([("is_mail_thread", "=", True)])
        return [(model.model, model.name) for model in models]

    def create_resource_link(self):
        for message in self.env['mail.message'].search(
            [('res_id', '=', self.message_id.res_id),
             ('model', '=', self.message_id.model),
             ('create_date', '>=', self.message_id.create_date),
             ('gateway_message_id', '=', False)]) | self.message_id:
            self.link_message(message=message)

    def link_message(self, message):
        message.gateway_message_id.unlink()
        message.gateway_message_id = self.resource_ref.message_post(
            body=message.body,
            author_id=message.author_id.id,
            email_from=self.env.user.partner_id.email,
            gateway_type=message.gateway_type,
            date=message.date,
            subtype_xmlid="mail.mt_comment",
            message_type="comment",
            attachment_ids=message.attachment_ids.ids,
            gateway_notifications=[],
        )
        message.gateway_message_id.parent_id = False
        self.env["bus.bus"]._sendmany([[partner, "mail.message/insert", {
            "id": message.id,
            "gateway_thread_data": message.sudo().gateway_thread_data,
        }] for partner in self.env[message.model].browse(message.res_id).channel_partner_ids])
