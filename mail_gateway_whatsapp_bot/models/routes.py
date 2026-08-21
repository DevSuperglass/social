import datetime

from odoo import models

_DAYS_OF_WEEK = [
    "Segunda-feira",
    "Terça-feira",
    "Quarta-feira",
    "Quinta-feira",
    "Sexta-feira",
    "Sábado",
    "Domingo",
]


class Routes(models.Model):
    _inherit = 'routes'

    def _compute_next_route_day_from(self, reference_date):
        """Reimplementação isolada de calcula_vencimento() (cotacoes), partindo
        de uma data de referência arbitrária em vez de sempre hoje — usada
        pra simular "amanhã" quando uma cotação do bot é criada depois do
        horário de corte do cron de envio ao Hitec. Não toca em cotacoes
        (verticalização do módulo do bot)."""
        self.ensure_one()

        if not self.route_deadline:
            return reference_date + datetime.timedelta(days=5)

        all_next_days = []
        for rec in self.route_deadline:
            next_day = reference_date + datetime.timedelta(days=1)
            while _DAYS_OF_WEEK[next_day.weekday()] != rec.dia:
                next_day += datetime.timedelta(days=1)
            all_next_days.append(next_day)
        return min(all_next_days)
