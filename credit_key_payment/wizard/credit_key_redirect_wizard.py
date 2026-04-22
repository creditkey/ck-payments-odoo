from odoo import fields, models


class CreditKeyRedirectWizard(models.TransientModel):
    _name = "credit.key.redirect.wizard"

    message = fields.Html()
