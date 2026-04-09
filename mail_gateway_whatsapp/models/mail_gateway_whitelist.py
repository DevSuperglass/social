from odoo import fields, models


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
        """Executa o método definido no formato 'model.nome_metodo' sobre o canal."""
        if not self.code:
            return
        method_name = self.code.strip().removeprefix('model.').strip()
        getattr(channel, method_name)(message_id)
