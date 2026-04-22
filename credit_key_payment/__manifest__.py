# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    "name": "Payment Provider: Credit Key",
    "version": "19.0.1.0.0",
    "category": "Accounting/Payment Providers",
    "sequence": 350,
    "author": "Credit Key",
    "website": "https://www.creditkey.com",
    "depends": ["sale_management", "website_sale"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/templates.xml",
        "data/account_payment_method_data.xml",
        "views/payment_credit_key_template.xml",
        "data/payment_provider_data.xml",
        "views/payment_form_templates.xml",
        "views/sale_order_views.xml",
        "views/account_move_views.xml",
        "views/payment_provider_views.xml",
        "wizard/credit_key_checkout_wizard_views.xml",
        "wizard/credit_key_status_wizard_views.xml",
        "wizard/credit_key_redirect_wizard_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "license": "LGPL-3",
}
