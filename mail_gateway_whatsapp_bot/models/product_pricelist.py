from odoo import fields, models


class ProductPricelist(models.Model):
    _inherit = 'product.pricelist'

    bot_accessible = fields.Boolean(
        string='Disponível para o Bot',
        default=False,
        help='Marque para permitir que o bot de cotações consulte esta lista de preços.'
    )
