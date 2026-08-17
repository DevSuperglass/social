from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    bot_whitelist_color = fields.Integer(string='Cor Whitelist Bot', default=0)

    def write(self, vals):
        rtn = super().write(vals)
        if 'phone' in vals or 'mobile' in vals:
            self._update_bot_whitelist_color()
        return rtn

    def _update_bot_whitelist_color(self):
        """
        Vermelho quando o filho de um contato principal da whitelist do bot
        não tem telefone/celular definido. Sem cor quando está ok.
        """
        whitelisted_parent_ids = self.env['mail.gateway.whitelist.contact'].search([
            ('parent_id', 'in', self.mapped('parent_ids').ids),
        ]).parent_id
        for partner in self:
            if not (partner.parent_ids & whitelisted_parent_ids):
                continue
            partner.bot_whitelist_color = 0 if (partner.phone or partner.mobile) else 1
