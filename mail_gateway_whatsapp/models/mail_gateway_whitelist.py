from odoo import api, fields, models


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
        string='Contatos',
        required=True,
    )
    allowed_partner_ids = fields.Many2many(
        'res.partner',
        compute='_compute_allowed_partner_ids',
        string='Partners disponíveis',
        help=(
            "Partners que já possuem registro em res.partner.gateway.channel "
            "para o gateway selecionado. Usado para restringir o domínio do "
            "campo Partners."
        ),
    )

    code = fields.Text(string='Código Python')

    @api.depends('gateway_id')
    def _compute_allowed_partner_ids(self):
        ChannelRel = self.env['res.partner.gateway.channel']
        for record in self:
            if not record.gateway_id:
                record.allowed_partner_ids = False
                continue
            rels = ChannelRel.search([('gateway_id', '=', record.gateway_id.id)])
            record.allowed_partner_ids = rels.mapped('partner_id')

    def execute(self, channel, message_id):
        """Executa o método definido no formato 'model.nome_metodo' sobre o canal."""
        if not self.code:
            return
        method_name = self.code.strip().rstrip('()').split('.')[-1].strip()
        getattr(channel, method_name)(message_id)
