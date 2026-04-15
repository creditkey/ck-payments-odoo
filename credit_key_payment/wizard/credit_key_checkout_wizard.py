from odoo import _, fields, models
from odoo.exceptions import UserError
import pprint

import requests
from urllib.parse import quote_plus


class CreditKeyCheckoutWizard(models.TransientModel):
    _name = "credit.key.checkout.wizard"
    _description = "Credit Key Checkout Wizard"

    invoice_id = fields.Many2one("account.move")
    partner_id = fields.Many2one("res.partner")

    credit_key_company_id = fields.Char()
    ck_company_name = fields.Char()
    ck_status = fields.Char()

    ck_borrower_name = fields.Char()
    ck_borrower_email = fields.Char()

    ck_tcl_amount = fields.Float()
    ck_tcl_remaining = fields.Float()
    ck_vc_limit = fields.Float()
    ck_vc_remaining = fields.Float()

    ck_ordering_available = fields.Boolean()
    ck_virtual_card_enabled = fields.Boolean()

    ck_available_terms = fields.Char()

    ck_decision_date = fields.Datetime()

    ck_other_companies = fields.Text()

    def action_confirm(self):
        """Make the Credit Key v2 backend checkout request."""
        self.ensure_one()
        provider = self.env["payment.provider"].search(
            [("code", "=", "credit_key")], limit=1
        )
        if not provider:
            raise UserError(_("No Credit Key payment provider found."))

        payload = self.invoice_id._credit_key_prepare_backend_checkout_payload(self.credit_key_company_id)
        print("\n ----------------------------------------", pprint.pformat(payload), "\n -------------------------------------")
        response = provider._credit_key_make_request(
            "order",
            payload=payload,
            api_version="v2",
        )
        print(response)
        if response.get("success") is False:
            print(response)
            status_code = response.get("status_code")
            message = response.get("message")
            error = response.get("error", "")
            if status_code and message:
                raise UserError(_("Order Placement failed due to - %s - %s", status_code, message))
            else:
                raise UserError(_("Order Placement failed: %s", error))

        ck_key = response.get("key")

        if not ck_key:
            raise UserError(_("Credit Key response missing order key."))

        invoice = self.invoice_id

        print("[CK] Setting CK Order ID on invoice:", ck_key)

        # Set on invoice
        invoice.write({
            "credit_key_order_id": ck_key,
        })

        # Get related sale orders
        sale_orders = invoice.invoice_line_ids.mapped("sale_line_ids.order_id")

        print("[CK] Related Sale Orders:", sale_orders.mapped("name"))

        if sale_orders:
            sale_orders.write({
                "credit_key_order_id": ck_key,
            })

        print("[CK] CK Order ID set successfully")

        status = (response.get("status") or "").upper()
        reasons = response.get("reasons") or []

        print("[CK] Status:", status, "| Reasons:", reasons)

        status = (response.get("status") or "").upper()
        reasons = response.get("reasons") or []

        if status == "PENDING":
            reason_text = ", ".join(reasons) if reasons else "Unknown reason"
            warning_msg = _(
                "Credit Key order is pending approval.\nReason: %s."
            ) % reason_text
            self.invoice_id.message_post(body=warning_msg)
            print("[CK WARNING]:", warning_msg)
        #TODO post success handling
        return {"type": "ir.actions.act_window_close"}

    # def credit_key_check_company(self):
    #     """Search for a Credit Key company by email via the v2 API.

    #     Sets credit_key_company_id on the wizard if a match is found,
    #     which will reveal the Confirm button and hide this button.
    #     """
    #     self.ensure_one()

    #     if not self.email:
    #         raise UserError(_("Please provide an email address to search for a Credit Key company."))

    #     provider = self.env["payment.provider"].search(
    #         [("code", "=", "credit_key")], limit=1
    #     )
    #     if not provider:
    #         raise UserError(_("No Credit Key payment provider found."))

    #     response = provider._credit_key_make_request(
    #         "company",
    #         payload={"search": self.email, "page": 1, "per_page": 25},
    #         api_version="v2",
    #     )

    #     if response.get("success") is False:
    #         status_code = response.get("status_code")
    #         message = response.get("message")
    #         error = response.get("error", "")
    #         if status_code and message:
    #             raise UserError(_("Company Check Failed due to - %s - %s", status_code, message))
    #         else:
    #             raise UserError(_("Credit Key company check failed: %s", error))

    #     data = response.get("data", [])
    #     if not data:
    #         raise UserError(_("No Credit Key company found for %s.", self.email))

    #     if len(data) == 1:
    #         self.credit_key_company_id = data[0].get("id")
    #     else:
    #         match = next(
    #             (c for c in data if c.get("borrower", {}).get("email") == self.email),
    #             None,
    #         )
    #         if not match:
    #             raise UserError(
    #                 _("Multiple companies found for %s but none matched exactly. Please check E-mail.", self.email)
    #             )
    #         self.credit_key_company_id = match.get("id")

    def credit_key_check_company(self):
        """Search for a Credit Key company by email via the v2 API."""
        self.ensure_one()

        print("\n================ CK COMPANY CHECK START ================")
        print("[CK] Input Email:", self.email)

        if not self.email:
            raise UserError(_("Please provide an email address to search for a Credit Key company."))

        provider = self.env["payment.provider"].search(
            [("code", "=", "credit_key")], limit=1
        )

        print("[CK] Provider found:", provider)

        if not provider:
            raise UserError(_("No Credit Key payment provider found."))

        payload = {
            "search": self.email,
            "page": 1,
            "per_page": 25,
        }

        print("[CK] Request Payload:", payload)

        response = provider._credit_key_make_request(
            "company",
            payload=payload,
            api_version="v2",
        )

        print("[CK] Raw Response:", response)

        # ---- ERROR HANDLING ----
        if response.get("success") is False:
            status_code = response.get("status_code")
            message = response.get("message")
            error = response.get("error", "")

            print("[CK] ERROR DETECTED")
            print("[CK] status_code:", status_code)
            print("[CK] message:", message)
            print("[CK] error:", error)

            if status_code and message:
                raise UserError(_("Company Check Failed due to - %s - %s", status_code, message))
            else:
                raise UserError(_("Credit Key company check failed: %s", error))

        # ---- DATA EXTRACTION ----
        data = response.get("data", [])

        print("[CK] Extracted Data:", data)
        print("[CK] Number of Companies Found:", len(data))

        if not data:
            raise UserError(_("No Credit Key company found for %s.", self.email))

        # ---- SINGLE RESULT ----
        if len(data) == 1:
            print("[CK] Single company found:", data[0])
            self.partner_id.credit_key_company_id = data[0].get("id")
            print("[CK] Selected Company ID:", self.credit_key_company_id)

        # ---- MULTIPLE RESULTS ----
        else:
            print("[CK] Multiple companies found. Attempting exact email match...")

            for c in data:
                print("[CK] Checking company:", c.get("id"), "| email:", c.get("borrower", {}).get("email"))

            match = next(
                (c for c in data if c.get("borrower", {}).get("email") == self.email),
                None,
            )

            print("[CK] Match Found:", match)

            if not match:
                raise UserError(
                    _("Multiple companies found for %s but none matched exactly. Please check E-mail.", self.email)
                )

            self.partner_id.credit_key_company_id = match.get("id")
            print("[CK] Selected Company ID:", self.credit_key_company_id)

        print("================ CK COMPANY CHECK END =================\n")
    
    def credit_key_check_company_1(self):
        base_url = "https://api.staging.creditkey.com/v2/company"

        search_email = "dlancaster+netsuite@testcompany.com"

        params = {
            "search": search_email,
            "page": 1,
            "per_page": 25,
        }

        headers = {
            "accept": "application/json",
            "Authorization": "Bearer LzMnYE1c3qx1aPCnO44_xgJsJ5-zjcjYV2pKVdVUWak",
        }

        try:
            response = requests.get(base_url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise UserError(f"CreditKey API Error: {str(e)}")