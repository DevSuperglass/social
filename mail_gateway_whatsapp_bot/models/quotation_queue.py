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

    def calculate_response_times(self):
        self.ensure_one()

        messages = self.env['mail.message'].search([
            ('res_id', '=', self.channel_id.id),
            ('model', '=', 'mail.channel'),
            ('message_type', '=', 'comment'),
            ('id', '>=', self.start_message_id.id),
            ('id', '<=', self.end_message_id.id),
        ], order='id asc')

        total_response_time = 0
        response_count = 0
        longest_response_time = 0
        longest_response_message = None
        prev_message = None

        for message in messages:
            if prev_message and message.author_id != prev_message.author_id:
                response_time = (message.date - prev_message.date).total_seconds()
                total_response_time += response_time
                response_count += 1
                if response_time > longest_response_time:
                    longest_response_time = response_time
                    longest_response_message = message
            prev_message = message

        average_response_time = total_response_time / response_count if response_count else 0
        self.write({
            'average_response_time': average_response_time,
            'longest_response_time': longest_response_time,
            'longest_response_message_id': longest_response_message.id if longest_response_message else False,
            'message_count': len(messages),
        })
