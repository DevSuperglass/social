# Copyright 2024 Dixmit
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import fields, models


class MailGateway(models.Model):

    _inherit = "mail.gateway"
    _description = "Modelo adicionado automaticamente"

    telegram_security_key = fields.Char()
    gateway_type = fields.Selection(
        selection_add=[("telegram", "Telegram")], ondelete={"telegram": "cascade"}
    )
