from odoo import api, fields, models


class MailGatewayWhitelist(models.Model):
    _inherit = 'mail.gateway.whitelist'

    contact_line_ids = fields.One2many(
        'mail.gateway.whitelist.contact',
        'whitelist_id',
        string='Clientes do Bot',
    )

    def write(self, vals):
        old_lines_by_record = {}
        old_covered_partners_by_record = {}
        if 'contact_line_ids' in vals:
            old_lines_by_record = {record.id: record.contact_line_ids for record in self}
            # Precisa ser resolvido em partners (não guardar as linhas em si)
            # ANTES do write: se a remoção do o2m deletar as linhas (comum,
            # já que whitelist_id é required), acessar parent_id/children_ids
            # nelas depois do write dispararia MissingError.
            old_covered_partners_by_record = {
                record.id: record.contact_line_ids.parent_id | record.contact_line_ids.children_ids
                for record in self
            }

        old_partners_by_record = {record.id: record.partner_ids for record in self}

        rtn = super().write(vals)

        for record in self:
            if record.name != 'ChatBot':
                continue

            new_lines = record.contact_line_ids - old_lines_by_record.get(record.id, record.contact_line_ids)
            if new_lines:
                record._sync_partner_ids_from_lines(new_lines)

            if 'contact_line_ids' in vals:
                still_covered = record.contact_line_ids.parent_id | record.contact_line_ids.children_ids
                removed_partners = old_covered_partners_by_record.get(record.id, still_covered) - still_covered
                if removed_partners:
                    record._unsync_partner_ids(removed_partners)

            new_partners = record.partner_ids - old_partners_by_record.get(record.id, record.partner_ids)
            if new_partners:
                record._normalize_channels_for_bot(new_partners)
        return rtn

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            if record.name != 'ChatBot':
                continue

            if record.contact_line_ids:
                record._sync_partner_ids_from_lines(record.contact_line_ids)

            if record.partner_ids:
                record._normalize_channels_for_bot(record.partner_ids)
        return records

    def _sync_partner_ids_from_lines(self, lines):
        """
        Popula partner_ids a partir das linhas (pai + filhos) recém-adicionadas
        em contact_line_ids. Usa o write da classe pai diretamente para não
        reentrar no nosso próprio write() — a normalização dos canais roda
        uma única vez, no fim do write()/create() que chamou este método,
        baseada no diff real de partner_ids.
        """
        self.ensure_one()
        partners = lines.parent_id | lines.children_ids
        if not partners:
            return
        super(MailGatewayWhitelist, self).write({'partner_ids': [(4, partner.id) for partner in partners]})
        lines.children_ids._update_bot_whitelist_color()

    def _unsync_partner_ids(self, partners):
        """
        Remove de partner_ids os partners que não são mais cobertos por
        nenhuma linha restante em contact_line_ids (pai removido ou filho que
        saiu de child_ids) — espelha _sync_partner_ids_from_lines, mas pra
        remoção. Usa o write da classe pai diretamente para não reentrar no
        nosso próprio write().
        """
        self.ensure_one()
        if not partners:
            return
        super(MailGatewayWhitelist, self).write({'partner_ids': [(3, partner.id) for partner in partners]})

    def _normalize_channels_for_bot(self, partners):
        """
        Ao vincular um cliente à whitelist do bot (via contact_line_ids ou
        diretamente em partner_ids), normaliza os canais desse cliente
        (encerra atendimento/fila em andamento) para que ele volte a ser
        tratado pelo bot em vez de permanecer preso a um vendedor/fila
        anterior. Roda apenas sobre os partners recém-adicionados.
        """
        self.ensure_one()
        if not partners:
            return

        channels = self.env['mail.channel'].search([
            ('channel_member_ids.partner_id', 'in', partners.ids),
            ('gateway_id', '=', self.gateway_id.id),
        ])
        if not channels:
            return

        for channel in channels.filtered('queue_id'):
            channel.delete_password_queue()

        channels.write({'seller_id': False, 'queue_id': False, 'attendance_type': False})

        # Fecha filas ainda abertas cujo channel_id.queue_id já estava dessincronizado
        # (não cobertas por delete_password_queue, que só age via self.queue_id).
        queues = self.env['quotation.queue'].search([
            ('channel_id', 'in', channels.ids),
            ('end_date', '=', False),
        ])
        if queues:
            queues.write({'end_date': fields.Datetime.now(), 'active': False})
