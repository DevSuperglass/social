from odoo import fields, models, api


class MailGatewayWhitelistContact(models.Model):
    _name = 'mail.gateway.whitelist.contact'
    _description = 'Contato Pai/Filhos da Whitelist do Bot'

    whitelist_id = fields.Many2one(
        'mail.gateway.whitelist',
        string='Whitelist',
        required=True,
        ondelete='cascade',
    )
    parent_id = fields.Many2one(
        'res.partner',
        string='Contato Principal',
        required=True,
    )
    allowed_parent_ids = fields.Many2many(
        'res.partner',
        compute='_compute_allowed_parent_ids',
        string='Contatos principais disponíveis',
        help=(
            "Partners do tipo empresa. "
            "Usado para restringir o domínio do campo Contato Principal."
        ),
    )
    children_ids = fields.Many2many(
        'res.partner',
        compute='_compute_children_ids',
        string='Contatos Filhos',
        store=True,
    )

    @api.depends("parent_id")
    def _compute_allowed_parent_ids(self):
        allowed = self.env['res.partner'].search([
            ('is_company', '=', True),
        ])
        for record in self:
            record.allowed_parent_ids = allowed

    @api.depends('parent_id', 'parent_id.child_ids')
    def _compute_children_ids(self):
        for record in self:
            record.children_ids = record.parent_id.child_ids
