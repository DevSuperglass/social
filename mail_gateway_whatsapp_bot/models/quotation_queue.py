from odoo import fields, models


class QuotationQueue(models.Model):
    _inherit = 'quotation.queue'

    attendance_type = fields.Selection(
        selection=[('bot', 'Bot'), ('human', 'Humano')],
        string='Tipo de atendimento',
        tracking=True
    )
    bot_escalated = fields.Boolean(
        string='Escalado pelo cliente',
        default=False,
        readonly=True,
        help="Marcado quando o cliente solicitou atendimento humano durante um atendimento do bot.",
    )
    next_queue_id = fields.Many2one(
        'quotation.queue',
        string='Próximo atendimento',
        readonly=True,
        help="Queue criado para o vendedor humano após escalada pelo cliente.",
    )

    def button_open_next_queue(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'quotation.queue',
            'res_id': self.next_queue_id.id,
            'view_mode': 'form',
            'target': 'current',
        }
