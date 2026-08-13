import logging
import requests

from odoo import models

_logger = logging.getLogger(__name__)


def _get_agent_config(env):
    """Retorna (url_base, secret) lidos do ir.config_parameter."""
    config = env['ir.config_parameter'].sudo()
    url = config.get_param('cotacoes.ai_agent_url', '').rstrip('/')
    secret = config.get_param('cotacoes.ai_agent_secret', '')
    return url, secret


def _notify_cache_refresh(env, cache_type: str):
    """Enfileira chamada ao /internal/refresh-cache após o commit da transação.

    Usa cr.postcommit para garantir que o DB já está atualizado quando o
    whatsapp_service reconstruir o cache a partir dele.
    """
    def _do_request():
        url_base, secret = _get_agent_config(env)
        if not url_base:
            _logger.warning("cotacoes.ai_agent_url não configurado — cache não notificado.")
            return
        try:
            resp = requests.post(
                f"{url_base}/internal/refresh-cache",
                params={"cache_type": cache_type},
                headers={"X-Webhook-Secret": secret},
                timeout=5,
            )
            resp.raise_for_status()
            _logger.info(
                "Cache '%s' notificado com sucesso: %s",
                cache_type, resp.json(),
            )
        except Exception as exc:
            _logger.warning(
                "Falha ao notificar refresh de cache '%s': %s",
                cache_type, exc,
            )

    env.cr.postcommit.add(_do_request)


class ProductAttributeValue(models.Model):
    """Notifica o whatsapp_service ao alterar valores de atributos do Odoo."""
    _name = 'product.attribute.value'
    _inherit = _name

    def create(self, vals_list):
        result = super().create(vals_list)
        _notify_cache_refresh(self.env, 'canonical')
        return result

    def write(self, vals):
        result = super().write(vals)
        _notify_cache_refresh(self.env, 'canonical')
        return result

    def unlink(self):
        result = super().unlink()
        _notify_cache_refresh(self.env, 'canonical')
        return result


class TemplateType(models.Model):
    """Notifica o whatsapp_service ao alterar tipos de template."""
    _name = 'template.type'
    _inherit = _name

    def create(self, vals_list):
        result = super().create(vals_list)
        _notify_cache_refresh(self.env, 'canonical')
        return result

    def write(self, vals):
        result = super().write(vals)
        _notify_cache_refresh(self.env, 'canonical')
        return result

    def unlink(self):
        result = super().unlink()
        _notify_cache_refresh(self.env, 'canonical')
        return result
