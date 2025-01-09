# Copyright 2024 Dixmit
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class MailMessageGatewayLink(models.TransientModel):
    _name = "mail.message.gateway.link"
    _description = "Link message from gateway"

    message_id = fields.Many2one("mail.message")
    resource_ref = fields.Reference(
        string="Record reference", selection="_selection_target_model"
    )

    @api.model
    def _selection_target_model(self):
        models = self.env["ir.model"].search([("is_mail_thread", "=", True)])
        return [(model.model, model.name) for model in models]

    def link_message(self):
        new_message = self.resource_ref.message_post(
            body=self.message_id.body,
            author_id=self.message_id.author_id.id,
            gateway_type=self.message_id.gateway_type,
            date=self.message_id.date,
            subtype_xmlid="mail.mt_comment",
            message_type="comment",
            attachment_ids=self.message_id.attachment_ids.ids,
            gateway_notifications=[],  # Avoid sending notifications
            email_from=self.env.user.partner_id.email,
        )
        self.message_id.gateway_message_id = new_message
        self.env["bus.bus"].sudo()._sendmany(
            [(partner, "mail.message/insert", {
                "id": self.message_id.id,
                "gateway_thread_data": self.message_id.sudo().gateway_thread_data,
            }) for partner in self.get_partner_ids()]
        )

    def get_partner_ids(self):
        channel_id = self.env['mail.channel'].browse(self.message_id.res_id)
        if channel_id.route_id:
            return channel_id.route_id.sales_employee_ids.mapped('user_id').mapped('partner_id')
        else:
            return self.env['mail.gateway'].search([]).member_ids.mapped('partner_id')
