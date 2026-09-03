from odoo import models


class ProjectRequest(models.Model):
    _inherit = 'project_request'

    def approve_quote(self):
        """Depois da aprovação normal (change_quote em cotacoes), se a
        cotação for do bot e a aprovação aconteceu depois do horário de
        corte do cron de envio ao Hitec do dia (ex.: gerproc aprovado
        depois das 16h30), dispara o envio direto em vez de esperar o
        próximo ciclo do cron — ver
        quotation._maybe_send_to_hitec_after_gerproc_approval()."""
        result = super().approve_quote()
        if self.quotation_id:
            self.quotation_id._maybe_send_to_hitec_after_gerproc_approval()
        return result
