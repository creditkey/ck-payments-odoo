# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    "name": "Payment Provider: Credit Key - V17",
    "version": "17.0.1.0.0",
    "category": "Accounting/Payment Providers",
    "sequence": 350,
    "author": "Credit Key",
    "website": "https://www.creditkey.com",
    "depends": ["website_sale"],
    "data": [
        "views/templates.xml",
        "data/account_payment_method_data.xml",
        "views/payment_credit_key_template.xml",
        "data/payment_provider_data.xml",
        "views/payment_form_templates.xml",
        "views/sale_order_views.xml",
        "views/account_move_views.xml",
        "views/payment_provider_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "license": "LGPL-3",
}
