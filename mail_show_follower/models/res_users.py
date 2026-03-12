from odoo import fields, models


class ResUser(models.Model):
    _inherit = "res.users"
    _description = "Extensão de Res Users (Social)"

    show_in_cc = fields.Boolean(string="Show in CC", default=True)
