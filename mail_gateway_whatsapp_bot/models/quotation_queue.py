from odoo import fields, models


class QuotationQueue(models.Model):
    _inherit = 'quotation.queue'

    attendance_type = fields.Selection(
        selection=[('bot', 'Bot'), ('human', 'Humano')],
        string='Tipo de atendimento',
        tracking=True
    )
