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
        'security/ir.model.access.csv',
        'data/cron.xml',
        'data/superglassbot_user.xml',
        'data/chatbot_whitelist.xml',
        'views/res_config_views.xml',
        'views/learned_alias.xml',
        'views/quotation_queue.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'mail_gateway_whatsapp_bot/static/src/models/thread.js',
            'mail_gateway_whatsapp_bot/static/src/models/messaging_notification_handler.js',
            'mail_gateway_whatsapp_bot/static/src/models/discuss_channel_attendance_filter.js',
        ],
    },
    'license': 'LGPL-3',
}
