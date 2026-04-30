from odoo import _, fields, models
from odoo.exceptions import UserError
import pprint

import requests
from urllib.parse import quote_plus


class CreditKeyCheckoutWizard(models.TransientModel):
    _name = "credit.key.checkout.wizard"
    _description = "Credit Key Checkout Wizard"

    sale_order_id = fields.Many2one("sale.order")
    partner_id = fields.Many2one("res.partner")

    credit_key_company_id = fields.Char(string="Company Id")
    ck_company_name = fields.Char(string="Company Name")
    ck_status = fields.Char(string="Status")

    ck_borrower_name = fields.Char(string="Borrower Name")
    ck_borrower_email = fields.Char(string="Email")

    ck_tcl_amount = fields.Float(string="TCL Amount")
    ck_tcl_remaining = fields.Float(string="TCL Remaining")
    support_link = fields.Char('Support Link', default="https://www.creditkey.com/support")

    def action_confirm(self):
        """Make the Credit Key v2 backend checkout request."""
        self.ensure_one()
        provider = self.env["payment.provider"].search(
            [("code", "=", "credit_key"),
            ("state", "in", ("test", "enabled"))], limit=1
        )
        if not provider:
            raise UserError(_("No Credit Key payment provider found."))

        payload = self.sale_order_id._credit_key_prepare_backend_checkout_payload(self.credit_key_company_id)
        response = provider._credit_key_make_request(
            "order",
            payload=payload,
            api_version="v2",
        )
        if response.get("success") is False:
            status_code = response.get("status_code")
            message = response.get("message")
            error = response.get("error", "")
            if status_code and message:
                raise UserError(_("Order Placement failed due to - %s - %s", status_code, message))
            else:
                raise UserError(_("Order Placement failed: %s", error))

        ck_key = response.get("key")
        status = "placed" if response.get("status") == "AUTHORIZED" else "failed to fetch"

        if not ck_key:
            raise UserError(_("Credit Key response missing order key."))

        sale_order = self.sale_order_id
        sale_order.write({
            "credit_key_order_id": ck_key,
            "credit_key_status": status,
        })
        status = (response.get("status") or "").upper()
        reasons = response.get("reasons") or []
        status = (response.get("status") or "").upper()
        reasons = response.get("reasons") or []

        if status == "PENDING":
            reason_text = ", ".join(reasons) if reasons else "Unknown reason"
            warning_msg = _(
                "Credit Key order is pending approval.\nReason: %s."
            ) % reason_text
            self.sale_order_id.message_post(body=warning_msg)
        return {"type": "ir.actions.act_window_close"}
