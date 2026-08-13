import logging

import requests as http_requests

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

WHATSAPP_SERVICE_URL = 'http://localhost:8000'
WHATSAPP_SERVICE_SECRET = '123'

_HEADERS = {
    'X-Webhook-Secret': WHATSAPP_SERVICE_SECRET,
    'Content-Type': 'application/json',
}

_ATTR_LABEL = {
    'cor':                      'COR',
    'modelo':                   'MODELO',
    'lado':                     'LADO',
    'posicao':                  'POSIÇÃO',
    'sensor':                   'SENSOR',
    'pastilha':                 'PASTILHA',
    'aquecedor':                'AQUECEDOR',
    'tipo_peca':                'TIPO DE PEÇA',
    'tipo':                     'TIPO',
    'serigrafia':               'SERIGRAFIA',
    'carroceria':               'CARROCERIA',
    'acessorio':                'ACESSÓRIO',
    'pvb':                      'PVB',
    'antena':                   'ANTENA',
    'guarnicao':                'GUARNIÇÃO',
    'serigrafia_pb':            'SERIGRAFIA PB',
    'serigrafia_vg':            'SERIGRAFIA VG',
    'furo':                     'FURO',
    'medidas':                  'MEDIDAS',
    'friso':                    'FRISO',
    'quimico':                  'QUÍMICO',
    'apresentacao':             'APRESENTAÇÃO',
    'tipo_borracha':            'TIPO BORRACHA',
    'caracteristica_borracha':  'CARACTERÍSTICAS BORRACHA',
    'material':                 'MATERIAL',
    'dureza':                   'NÍVEL DE DUREZA',
    'alfanumerico_simbolo':     'ALFANUMÉRICO E SÍMBOLOS',
    'caracteristica_canaleta':  'CARACTERÍSTICAS CANALETA',
    'caracteristica_pingadeira':'CARACTERÍSTICAS PINGADEIRA',
    'caracteristica_pestana':   'CARACTERÍSTICAS PESTANA',
    'tipo_encaixe':             'TIPO DE ENCAIXE',
    'discard':                  'DESCARTE',
    'intent':                   'INTENÇÃO',
}

_TIPO_FROM_ATTRIBUTE = {
    'discard': 'discard',
    'intent':  'intent',
}


class LearnedAlias(models.Model):
    _name = 'learned.alias'
    _description = 'Aliases Aprendidos pela IA'
    _order = 'create_date desc, attribute, term'

    attribute = fields.Char('Atributo Interno', readonly=True)
    attribute_label = fields.Char(
        string='Tipo',
        compute='_compute_attribute_label',
        store=True,
    )
    tipo = fields.Selection([
        ('attribute', 'Atributo'),
        ('discard',   'Descarte'),
        ('intent',    'Intenção'),
    ], string='Categoria', compute='_compute_tipo', store=True, readonly=True)
    term = fields.Char('Valor Recebido', readonly=True)
    correction = fields.Char('Correção da IA', readonly=True)
    original_message = fields.Char('Mensagem do Cliente', readonly=True)
    source = fields.Selection([
        ('llm',   'LLM (DeepSeek)'),
        ('fuzzy', 'Regex Fuzzy'),
    ], string='Origem', readonly=True, default='llm')

    new_attribute = fields.Selection(
        selection=[(k, v) for k, v in sorted(_ATTR_LABEL.items(), key=lambda x: x[1])],
        string='Readequar Tipo',
        help='Deixe em branco para manter o tipo original identificado pela IA.',
    )
    new_correction = fields.Char(
        string='Readequar Correção',
        help='Deixe em branco para manter a correção original da IA.',
    )

    @api.depends('attribute')
    def _compute_attribute_label(self):
        for rec in self:
            rec.attribute_label = _ATTR_LABEL.get(
                rec.attribute, (rec.attribute or '').upper()
            )

    @api.depends('attribute')
    def _compute_tipo(self):
        for rec in self:
            rec.tipo = _TIPO_FROM_ATTRIBUTE.get(rec.attribute or '', 'attribute')

    def action_accept(self):
        for rec in self:
            attribute  = rec.new_attribute  or rec.attribute
            correction = rec.new_correction or rec.correction or ''
            rec._call_service('accept', {
                'attribute':  attribute,
                'term':       rec.term,
                'correction': correction,
            })
        self.unlink()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_reject(self):
        for rec in self:
            rec._call_service('reject', {
                'attribute': rec.attribute,
                'term': rec.term,
            })
        self.unlink()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def _call_service(self, action: str, payload: dict):
        url = f'{WHATSAPP_SERVICE_URL}/internal/alias/{action}'
        try:
            resp = http_requests.post(url, json=payload, headers=_HEADERS, timeout=10)
            resp.raise_for_status()
        except http_requests.HTTPError as e:
            detail = ''
            try:
                detail = e.response.json().get('detail', '')
            except Exception:
                pass
            raise UserError(f'Erro ao {action} alias "{payload.get("term")}": {detail or e}')
        except Exception as e:
            raise UserError(f'Erro ao conectar no whatsapp_service: {e}')
