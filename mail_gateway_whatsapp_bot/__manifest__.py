{
    'name': 'Mail Gateway WhatsApp Bot',
    'version': '16.0.1.0.0',
    'summary': 'Integração do bot de cotações via WhatsApp com o agente IA',
    'category': 'WhatsApp',
    'author': 'Superglass',
    'depends': [
        'cotacoes',
        'mail_gateway_whatsapp',
    ],
    'data': [
        'data/cron.xml',
        'views/res_config_views.xml',
    ],
    'license': 'LGPL-3',
}
