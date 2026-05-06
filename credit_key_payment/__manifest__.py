# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    "name": "Payment Provider: Credit Key",
    "version": "19.0.1.0.0",
    "category": "Accounting/Payment Providers",
    "summary": "B2B payment terms at checkout",
    "description": "Credit Key is a B2B payment solution that enables merchants to offer fast, transparent, and flexible payment terms to business buyers without taking on credit risk. With Credit Key integrated into Odoo, your sales reps can offer Net 30 and extended payment terms, and complete the full payment flow directly within Odoo — no switching systems, no manual credit checks, no offline approvals. The result is higher conversion rates, larger average order values, and faster purchasing decisions.",
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
    'images': ['static/description/images/banner.png'],
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "license": "LGPL-3",
    "application": True,
    "installable": True
}
