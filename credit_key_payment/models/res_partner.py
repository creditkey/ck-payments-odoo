from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    credit_key_company_id = fields.Char("Company ID", readonly=True)
