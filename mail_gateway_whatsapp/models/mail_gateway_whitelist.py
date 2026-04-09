from odoo import fields, models
from odoo.tools.safe_eval import safe_eval


class MailGatewayWhitelist(models.Model):
    _name = 'mail.gateway.whitelist'
    _description = 'Regra de Whitelist do Gateway'

    name = fields.Char(string='Nome', required=True)
    gateway_id = fields.Many2one(
        'mail.gateway',
        string='Gateway',
        required=True,
        ondelete='cascade',
    )
    partner_ids = fields.Many2many(
        'res.partner',
        string='Partners',
        required=True,
    )

    code = fields.Text(string='Código Python')

    def execute(self, channel, message_id):
        """Executa o código da regra com o contexto do canal e da mensagem."""
        if not self.code:
            return
        local_ctx = {
            'env': self.env,
            'channel': channel,
            'message_id': message_id,
        }
        safe_eval(self.code, local_ctx, mode='exec', nocopy=True)
