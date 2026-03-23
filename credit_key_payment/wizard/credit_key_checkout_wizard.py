from odoo import fields, models


class CreditKeyCheckoutWizard(models.TransientModel):
    _name = "credit.key.checkout.wizard"
    _description = "Credit Key Checkout Wizard"

    sale_order_id = fields.Many2one("sale.order", readonly=True)
    credit_key_company_id = fields.Char(related="sale_order_id.credit_key_company_id")
    partner_name = fields.Char(string="Customer Name")
    email = fields.Char(string="Email")
    phone = fields.Char(string="Phone")
    street = fields.Char(string="Address")
    street2 = fields.Char(string="Address 2")
    city = fields.Char(string="City")
    state_id = fields.Many2one("res.country.state", string="State")
    zip = fields.Char(string="Zip")

    def action_confirm(self):
        self.ensure_one()
        # your checkout logic here
        return {"type": "ir.actions.act_window_close"}

    def credit_key_check_company(self):
        """Search for a Credit Key company by email via the v2 API.

        Sets credit_key_company_id on the wizard if a match is found,
        which will reveal the Confirm button and hide this button.
        """
        self.ensure_one()

        if not self.email:
            raise UserError(_("Please provide an email address to search for a Credit Key company."))

        provider = self.env["payment.provider"].search(
            [("code", "=", "credit_key")], limit=1
        )
        if not provider:
            raise UserError(_("No Credit Key payment provider found."))

        response = provider._credit_key_make_request(
            "company",
            payload={"search": self.email, "page": 1, "per_page": 25},
            api_version="v2",
        )

        if response.get("success") is False:
            status_code = response.get("status_code")
            error = response.get("error", "")
            if status_code == 400:
                raise UserError(_("Invalid search parameter. Please provide a valid email."))
            elif status_code == 404:
                raise UserError(_("No Credit Key company found for %s.", self.email))
            else:
                raise UserError(_("Credit Key company check failed: %s", error))

        data = response.get("data", [])
        if not data:
            raise UserError(_("No Credit Key company found for %s.", self.email))

        if len(data) == 1:
            self.credit_key_company_id = data[0].get("id")
        else:
            match = next(
                (c for c in data if c.get("borrower", {}).get("email") == self.email),
                None,
            )
            if not match:
                raise UserError(
                    _("Multiple companies found for %s but none matched exactly. Please refine your search.", self.email)
                )
            self.credit_key_company_id = match.get("id")
