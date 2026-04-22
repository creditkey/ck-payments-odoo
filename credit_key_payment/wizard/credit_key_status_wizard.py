from odoo import fields, models


class CreditKeyStatusWizard(models.TransientModel):
    _name = "credit.key.status.wizard"
    _description = "Credit Key Status"

    sale_order_id = fields.Many2one("sale.order")
    credit_key_order_id = fields.Char("Credit Key Order ID")
    credit_key_status = fields.Char("Status")
    message = fields.Text("Message")
