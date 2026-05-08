from odoo import fields, models


class CreditKeyRedirectWizard(models.TransientModel):
    _name = "credit.key.redirect.wizard"

    message = fields.Char()
    apply_message = fields.Html()
    url = fields.Char('URL', default="https://www.creditkey.com/app/users/sign_in")
    support_url = fields.Char('Support Link', default="https://www.creditkey.com/support")
