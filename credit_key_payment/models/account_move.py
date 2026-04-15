from odoo import _, fields, models
from odoo.exceptions import UserError
import json, re
from odoo.tools.float_utils import float_compare


class AccountMove(models.Model):
    _inherit = "account.move"

    credit_key_order_id = fields.Char("Credit Key Order ID", copy=False, readonly=True)
    credit_key_checkout_url = fields.Char("Credit Key Checkout URL", copy=False, readonly=True)
    credit_key_status = fields.Selection(
        [
            ("failed to fetch", "Failed to Fetch"),
            ("new", "New"),
            ("shipped", "Shipped"),
            ("placed", "Placed"),
            ("canceled", "Canceled"),
            ("refunded", "Refunded"),
            ("returned", "Returned"),
        ],
        copy=False,
        readonly=True,
    )
    credit_key_company_id = fields.Char("Company ID", related="partner_id.credit_key_company_id")

    def button_cancel(self):
        """Handle Credit Key refunds safely before Odoo cancels the invoice."""
        for move in self:
            if move.move_type != "out_invoice":
                continue
            ck_order_id = move.credit_key_order_id
            if not ck_order_id:
                continue
            provider = move.env["payment.provider"].sudo().search([("code", "=", "credit_key")], limit=1)
            if not provider:
                raise UserError(_("No Credit Key provider configured."))
            response = provider._credit_key_make_request(
                "find_order",
                {"id": ck_order_id},
            )
            if response.get("success") is False:
                raise UserError(_("Credit Key request failed: %s") % response.get("error"))
            order_status = (response.get("status") or "").lower()
            if order_status not in ("placed", "shipped"):
                if order_status == "new":
                    raise UserError(
                        _(
                            "Cannot refund Credit Key order %s because it is not yet "
                            "placed or shipped (current status: %s)."
                        )
                        % (ck_order_id, order_status)
                    )
                if order_status in ("canceled", "refunded", "returned"):
                    raise UserError(
                        _("Credit Key order %s is already %s — no refund needed.") % (ck_order_id, order_status)
                    )
                raise UserError(
                    _("Refund not allowed: Credit Key order %s is in invalid state '%s'.") % (ck_order_id, order_status)
                )
            payload = {
                "id": ck_order_id,
                "amount": float(move.amount_total),
            }
            refund_response = provider._credit_key_make_request(
                "refund",
                payload,
            )
            if refund_response.get("success") is False:
                raise UserError(_("Credit Key refund request failed: %s") % refund_response.get("error"))
            refund_status = (refund_response.get("status") or "").lower()
            if refund_status not in ("returned", "refunded", "canceled"):
                raise UserError(
                    _("Credit Key refund failed for invoice %s. Response status: %s") % (move.name, refund_status)
                )
            move.message_post(
                body=_("Credit Key refund successful for order %s. " "Amount refunded: %s%s")
                % (ck_order_id, move.currency_id.symbol, move.amount_total)
            )
            move.write({"credit_key_status": "refunded"})
        return super().button_cancel()

    def _credit_key_check_company(self):
        """Fetch Credit Key company data (moved from wizard)."""
        self.ensure_one()

        print("\n================ CK COMPANY CHECK (MOVE) START ================")
        print("[CK MOVE] Invoice:", self.name)
        print("[CK MOVE] Partner:", self.partner_id.name)
        print("[CK MOVE] Email:", self.partner_id.email)

        if not self.partner_id.email:
            raise UserError(_("Customer must have an email to check Credit Key company."))

        provider = self.env["payment.provider"].search(
            [("code", "=", "credit_key")], limit=1
        )

        print("[CK MOVE] Provider:", provider)

        if not provider:
            raise UserError(_("No Credit Key payment provider found."))

        payload = {
            "search": self.partner_id.email,
            "page": 1,
            "per_page": 25,
        }

        print("[CK MOVE] Payload:", payload)

        response = provider._credit_key_make_request(
            "company",
            payload=payload,
            api_version="v2",
        )

        print("[CK MOVE] RAW RESPONSE:", response)

        if response.get("success") is False:
            print("[CK MOVE] ERROR:", response)
            raise UserError(_("Credit Key company check failed: %s") % response.get("error"))

        data = response.get("data", [])

        print("[CK MOVE] DATA:", data)
        print("[CK MOVE] COUNT:", len(data))

        print("================ CK COMPANY CHECK (MOVE) END =================\n")

        return response


    def credit_key_checkout(self):
        self.ensure_one()

        response = self._credit_key_check_company()

        data = response.get("data", [])
        if not data:
            raise UserError(_("No Credit Key company found."))
        
        def _parse_ck_datetime(date_str):
            if not date_str:
                return False
            for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
                try:
                    return datetime.strptime(date_str, fmt)
                except Exception:
                    continue
            return False

        # ---- MAIN COMPANY ----
        company = data[0]
        borrower = company.get("borrower", {})

        borrower_name = f"{borrower.get('first_name', '')} {borrower.get('last_name', '')}".strip()

        context = {
            "default_invoice_id": self.id,
            "default_partner_id": self.partner_id.id,

            # ---- MAIN INFO ----
            "default_credit_key_company_id": company.get("id"),
            "default_ck_company_name": company.get("name"),
            "default_ck_status": company.get("status"),

            # ---- BORROWER ----
            "default_ck_borrower_name": borrower_name,
            "default_ck_borrower_email": borrower.get("email"),

            # ---- FINANCIAL ----
            "default_ck_tcl_amount": company.get("tcl_amount"),
            "default_ck_tcl_remaining": company.get("tcl_amount_remaining"),
            "default_ck_vc_limit": company.get("vc_sub_limit"),
            "default_ck_vc_remaining": company.get("vc_sub_limit_remaining"),

            # ---- FLAGS ----
            "default_ck_ordering_available": company.get("ordering_available"),
            "default_ck_virtual_card_enabled": company.get("virtual_card_enabled"),

            # ---- TERMS ----
            "default_ck_available_terms": ", ".join(map(str, company.get("available_terms", []))),

            # ---- DATE ----
            "default_ck_decision_date": _parse_ck_datetime(company.get("decision_date")),

            # ---- MULTIPLE COMPANIES ----
            "default_ck_other_companies": json.dumps(data[1:]) if len(data) > 1 else False,
        }

        return {
            "type": "ir.actions.act_window",
            "name": "Pay with Credit Key",
            "res_model": "credit.key.checkout.wizard",
            "view_mode": "form",
            "target": "new",
            "context": context,
        }

    def _credit_key_prepare_backend_checkout_payload(self, credit_key_company_id):
        """Prepare the checkout payload for Credit Key.

        :return: dict payload for v2 checkout API.
        :rtype: dict
        """
        self.ensure_one()
        sale_order = self.invoice_line_ids.sale_line_ids.order_id[:1]

        def _clean_phone(phone):
            if not phone:
                raise UserError(_("Phone Number missing on Customer, Please check."))
            phone = phone.strip()
            phone = re.sub(r"[^0-9\-]", "", phone)
            return phone

        def _format_address(partner):
            name_parts = partner.name.split() if partner.name else []
            return {
                "address1": partner.street or "",
                "address2": partner.street2 or "",
                "city": partner.city or "",
                "state": partner.state_id.code or partner.state_id.name or "",
                "zip": partner.zip or "",
                "first_name": name_parts[0] if name_parts else "",
                "last_name": name_parts[-1] if len(name_parts) > 1 else "",
                "company_name": partner.commercial_company_name or partner.company_name or "",
                "email": partner.email or "",
                "phone_number": _clean_phone(partner.phone or partner.mobile),
            }

        if sale_order and sale_order.partner_invoice_id != sale_order.partner_shipping_id:
            billing_address = _format_address(sale_order.partner_invoice_id)
            shipping_address = _format_address(sale_order.partner_shipping_id)
        else:
            address = _format_address(self.partner_id)
            billing_address = address
            shipping_address = address

        cart_items = []
        for line in self.invoice_line_ids.filtered(lambda l: l.display_type not in ("line_section", "line_subsection", "line_note")):
            tax_value = float(line.tax_ids and line.tax_base_amount or 0.0)
            print(tax_value, "====================================================")
            item = {
                "merchant_id": str(line.id),
                "name": line.product_id.display_name or line.name or "Item",
                "price": float(line.price_unit),
                "quantity": int(line.quantity),
                "sku": line.product_id.default_code or "",
                "size": line.product_id.product_tmpl_id.attribute_line_ids.filtered(
                    lambda a: a.attribute_id.name.lower() == "size"
                ).mapped("value_ids.name")[:1] or "",
                "color": line.product_id.product_tmpl_id.attribute_line_ids.filtered(
                    lambda a: a.attribute_id.name.lower() == "color"
                ).mapped("value_ids.name")[:1] or "",
            }
            if float_compare(tax_value, 0.0, precision_digits=2) > 0:
                item["tax"] = tax_value
            cart_items.append(item)

        return {
            "company_id": credit_key_company_id or sale_order.credit_key_company_id,
            "status": "CREATED",
            "amount": float(self.amount_total),
            "merchant_order_id": self.invoice_origin or (sale_order.name if sale_order else ""),
            "shipping_address": shipping_address,
            "billing_address": billing_address,
            "items": cart_items,
            "metadata": {
                "invoice_number": self.name,
                "sales_rep": self.invoice_user_id.name or self.env.user.name,
            },
        }
