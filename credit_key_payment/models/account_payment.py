from odoo import fields, models


class AccountPayment(models.Model):
    _inherit = "account.payment"

    refund_failed = fields.Boolean()
    refund_message = fields.Char()
