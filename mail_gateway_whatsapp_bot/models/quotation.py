import re

from odoo import models


class Quotation(models.Model):
    _inherit = 'quotation'

    def item_verification_response(self):
        """Estende o comportamento padrão (grava status_map na linha): em
        CONFIRMAR/DESISTIR, também fecha a fila do bot e avisa o chatbot para
        limpar a sessão — mas só quando o canal do clique realmente está em
        atendimento bot (evita fechar a fila errada se o time de logística
        clicar nos mesmos botões no template validacao_produto, que usa outro
        canal)."""
        button = self.env.context.get('button')
        waid = self.env.context.get('waid')

        result = super().item_verification_response()

        if button not in ('CONFIRMAR', 'DESISTIR'):
            return result

        message = self.env['mail.message'].search([('whatsapp_id', '=', waid)])
        if not message:
            return result

        quotation_match = re.search(r'Cotação Nº: (\d+)', message.body or '')
        if not quotation_match:
            return result

        quotation_id = self.browse(int(quotation_match.group(1)))
        if quotation_id.state in ('fully_approved', 'expired', 'review'):
            # Mesmo gate do método base: não houve alteração de status real.
            return result

        channel = self.env['mail.channel'].browse(message.res_id)
        if channel.exists() and channel.attendance_type == 'bot':
            channel.delete_password_queue()
            channel._notify_chatbot_clear_session(channel.id)

        return result
