from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare
import re, logging

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    credit_key_order_id = fields.Char()
    credit_key_status = fields.Char()
    credit_key_company_id = fields.Char("Company ID", related="partner_id.credit_key_company_id")

    # def _create_invoices(self, grouped=False, final=False, date=None):
    #     invoices = super()._create_invoices(grouped=grouped, final=final, date=date)

    #     for so in self:
    #         credit_key_tx = so.transaction_ids.filtered(lambda tx: tx.provider_id.code == "credit_key")[:1]
    #         ck_order_id = so.credit_key_order_id if so.credit_key_order_id else credit_key_tx.credit_key_order_id
    #         if not credit_key_tx and not ck_order_id:
    #             continue

    #         provider = self.env["payment.provider"].search(
    #             [("code", "=", "credit_key"),
    #             ("state", "in", ("test", "enabled"))], limit=1
    #         )
    #         linked_invoices = invoices.filtered(lambda inv: inv.invoice_origin == so.name)

    #         if linked_invoices:
    #             find_payload = {"id": ck_order_id}
    #             find_resp = provider._credit_key_make_request("find_order", payload=find_payload)
    #             if find_resp.get("success") is False:
    #                 error_text = find_resp.get("error")
    #                 raise UserError(_("Credit Key request failed: %s") % error_text)
    #             current_status = find_resp.get("status", "failed to fetch")
    #             ck_total = float(find_resp.get("charges", {}).get("grand_total", 0.0))
    #             backend_amount = credit_key_tx.amount if credit_key_tx else so.amount_total
    #             difference = round(ck_total - backend_amount, 2)
    #             if difference:
    #                 credit_key_tx = so._ck_recreate_transaction(credit_key_tx, invoices, ck_total)

    #             linked_invoices.write({
    #                 "credit_key_order_id": ck_order_id,
    #                 "credit_key_status": current_status,
    #             })
    #             linked_invoices.message_post(
    #                 body=_("Linked to Credit Key order %s with CK status '%s'.") % (ck_order_id, current_status)
    #             )
    #         deliverable_lines = so.order_line.filtered(lambda l: l.product_id.invoice_policy == "delivery")

    #         all_delivered = all(line.qty_delivered >= line.product_uom_qty for line in deliverable_lines)

    #         if deliverable_lines and not all_delivered:
    #             continue

    #         confirm_resp = credit_key_tx._call_credit_key_confirm_order(provider, so, ck_order_id)
    #         if confirm_resp.get("success") is False:
    #             error_text = confirm_resp.get("error")
    #             so.message_post(body=_("Credit Key /confirm_order failed for %s: %s") % (ck_order_id, error_text))
    #             raise UserError(_("Create Invoice failed %s:", error_text))
    #         final_status = confirm_resp.get("status")
    #         so.credit_key_status = final_status
    #         so.message_post(body=_("Credit Key order %s confirmed after full delivery.") % ck_order_id)
    #         if linked_invoices:
    #             linked_invoices.write({"credit_key_status": final_status})
    #     return invoices

    def _create_invoices(self, grouped=False, final=False, date=None):
        invoices = super()._create_invoices(grouped=grouped, final=final, date=date)

        for so in self:
            credit_key_tx = so.transaction_ids.filtered(lambda tx: tx.provider_id.code == "credit_key")[:1]
            ck_order_id = so.credit_key_order_id or credit_key_tx.credit_key_order_id

            if not ck_order_id:
                continue

            provider = self.env["payment.provider"].search(
                [("code", "=", "credit_key"), ("state", "in", ("test", "enabled"))],
                limit=1,
            )
            linked_invoices = invoices.filtered(lambda inv: inv.invoice_origin == so.name)
            find_resp = provider._credit_key_make_request("find_order", payload={"id": ck_order_id})

            if find_resp.get("success") is False:
                raise UserError(_("Credit Key request failed: %s") % find_resp.get("error"))

            current_status = find_resp.get("status", "failed to fetch")
            ck_total = float(find_resp.get("charges", {}).get("grand_total", 0.0))
            backend_amount = credit_key_tx.amount if credit_key_tx else so.amount_total
            difference = round(ck_total - backend_amount, 2)

            if credit_key_tx:
                if difference:
                    credit_key_tx = so._ck_recreate_transaction(credit_key_tx, invoices, ck_total)
            else:
                credit_key_tx = so._ck_create_tx_and_payment(
                    provider=provider,
                    ck_order_id=ck_order_id,
                    amount=ck_total,
                    invoices=invoices,
                )

            if linked_invoices:
                linked_invoices.write({
                    "credit_key_order_id": ck_order_id,
                    "credit_key_status": current_status,
                })
                linked_invoices.message_post(
                    body=_("Linked to Credit Key order %s with CK status '%s'.") % (ck_order_id, current_status)
                )

            deliverable_lines = so.order_line.filtered(
                lambda l: l.product_id.invoice_policy == "delivery"
            )

            all_delivered = all(
                line.qty_delivered >= line.product_uom_qty for line in deliverable_lines
            )

            if deliverable_lines and not all_delivered:
                continue

            confirm_resp = credit_key_tx._call_credit_key_confirm_order(provider, so, ck_order_id)
            if confirm_resp.get("success") is False:
                error_text = confirm_resp.get("error")
                so.message_post(body=_("Credit Key /confirm_order failed for %s: %s") % (ck_order_id, error_text))
                raise UserError(_("Create Invoice failed %s:", error_text))
            final_status = confirm_resp.get("status")
            so.credit_key_status = final_status
            so.message_post(body=_("Credit Key order %s confirmed after full delivery.") % ck_order_id)
            if linked_invoices:
                linked_invoices.write({"credit_key_status": final_status})
        return invoices

    def _ck_create_tx_and_payment(self, provider, ck_order_id, amount, invoices):
        self.ensure_one()

        _logger.warning("[CK BACKEND] Creating transaction + payment for SO %s", self.name)
        if not provider.payment_method_ids:
            raise UserError(_("Please Make sure the Payment Method is set on the Provider."))
        ck_payment_method = provider.payment_method_ids.filtered(lambda p: p.code == "payment_credit_key")

        tx = self.env["payment.transaction"].create({
            "provider_id": provider.id,
            "payment_method_id": ck_payment_method[0].id,
            "provider_code": "credit_key",
            "reference": f"{self.name}-CK",
            "amount": amount,
            "currency_id": self.currency_id.id,
            "partner_id": self.partner_id.id,
            "company_id": self.company_id.id,
            "state": "draft",
            "credit_key_order_id": ck_order_id,
        })
        tx.sale_order_ids = [(6, 0, self.ids)]
        invoice = invoices.filtered(lambda inv: inv.invoice_origin == self.name)[:1]

        if invoice:
            tx.invoice_ids = [(6, 0, invoice.ids)]

        journal = provider.journal_id
        payment_vals = {
            "payment_type": "inbound",
            "partner_type": "customer",
            "partner_id": self.partner_id.id,
            "amount": amount,
            "currency_id": self.currency_id.id,
            "payment_method_line_id": journal.inbound_payment_method_line_ids[:1].id,
            "journal_id": journal.id,
            "date": fields.Date.today(),
            "memo": f"CK-{self.name}",
        }
        payment = self.env["account.payment"].create(payment_vals)
        payment.action_post()
        tx.payment_id = payment.id
        tx._set_done()
        tx._post_process()

        _logger.warning("[CK BACKEND] Transaction %s and Payment %s created", tx.reference, payment.name)

        return tx

    def _ck_recreate_transaction(self, credit_key_tx, invoices, ck_total):
        self.ensure_one()

        if not credit_key_tx:
            return False

        old_tx = credit_key_tx
        if float_compare(old_tx.amount, ck_total, precision_digits=2) == 0:
            return old_tx

        old_tx._set_canceled(
            state_message="Recreating transaction due to update.",
            extra_allowed_states=("done",),
        )

        if old_tx.payment_id:
            old_tx.payment_id.action_draft()
            old_tx.payment_id.action_cancel()

        new_tx = old_tx.copy({
            "payment_id": False,
            "amount": ck_total,
            "reference": old_tx.reference + "-ADJ",
            "state": "draft",
            "credit_key_order_id": old_tx.credit_key_order_id,
        })
        old_tx.sale_order_ids = [(5, 0, 0)]
        old_tx.unlink()
        new_tx.sale_order_ids = [(6, 0, self.ids)]
        invoice = invoices.filtered(lambda inv: inv.invoice_origin == self.name)[:1]
        if invoice:
            new_tx.invoice_ids = [(6, 0, invoice.ids)]
        new_tx._set_done()
        new_tx._post_process()
        return new_tx

    def write(self, vals):
        res = super().write(vals)
        tracked_fields = {"partner_id", "partner_shipping_id", "order_line"}
        if not any(field in vals for field in tracked_fields) and not self.env.context.get("trigger_ck_update"):
            return res
        for order in self:
            if order.state != "sale":
                continue
            if not order.credit_key_order_id:
                continue
            provider = self.env["payment.provider"].search(
                [("code", "=", "credit_key"),
                ("state", "in", ("test", "enabled"))], limit=1
            )
            if not provider:
                raise UserError(_("No Credit Key provider found."))
            ck_order_id = order.credit_key_order_id
            result = provider._credit_key_make_request("find_order", {"id": ck_order_id})
            if result.get("success") is False:
                raise UserError(_("Credit Key request failed: %s") % result.get("error"))
            status = (result.get("status") or "").lower()
            if status not in ("placed", "new", "authorized"):
                raise UserError(_("Cannot modify this order because its Credit Key status is '%s'.") % status)
            payload = order._credit_key_prepare_update_payload(status)
            update_res = provider._credit_key_make_request("update_order", payload)
            if update_res.get("success") is False:
                raise UserError(_("Failed to update Credit Key order: %s") % update_res.get("error"))
        return res

    def _credit_key_prepare_update_payload(self, status=None):
        """Prepare /update_order payload for Credit Key based on the sale order.

        :param status: (optional) The Credit Key order status from /find_order.
        :return: dict payload for /update_order API.
        """
        self.ensure_one()

        partner = self.partner_shipping_id or self.partner_id

        # --- Cart items ---
        cart_items = []
        for line in self.order_line.filtered(lambda l: not l.display_type and l.price_subtotal > 0):
            tax_amount = line.price_tax if line.tax_ids else 0.0
            cart_items.append({
                "merchant_id": str(line.id),
                "name": line.product_id.display_name or line.name or "Item",
                "price": float(line.price_subtotal),
                "quantity": int(line.product_uom_qty),
                "sku": line.product_id.default_code or "",
                "tax": float(tax_amount),
                "size": line.product_template_id.attribute_line_ids.filtered(
                    lambda a: a.attribute_id.name.lower() == "size"
                ).mapped("value_ids.name")[:1]
                or "",
                "color": line.product_template_id.attribute_line_ids.filtered(
                    lambda a: a.attribute_id.name.lower() == "color"
                ).mapped("value_ids.name")[:1]
                or "",
            })
        shipping = sum(l.price_total for l in self.order_line.filtered(lambda l: l.is_delivery))

        # --- Charges ---
        charges = {
            "total": float(self.amount_untaxed),
            "shipping": shipping,
            "tax": float(self.amount_tax),
            "discount_amount": self.currency_id.round(self.amount_undiscounted - self.amount_untaxed) if self.amount_undiscounted else 0.0,
            "grand_total": float(self.amount_total),
        }

        # --- Shipping address ---
        shipping_address = {
            "first_name": (partner.name or "").split(" ")[0] if partner.name else "",
            "last_name": (partner.name or "").split(" ")[-1] if partner.name else "",
            "company_name": partner.commercial_company_name or partner.company_name or "",
            "email": partner.email or "",
            "address1": partner.street or "",
            "address2": partner.street2 or "",
            "city": partner.city or "",
            "state": partner.state_id.name or partner.state_id.code or "",
            "zip": partner.zip or "",
            "phone_number": partner.phone or partner.mobile or "",
        }

        return {
            "cart_items": cart_items,
            "charges": charges,
            "shipping_address": shipping_address,
            "id": self.credit_key_order_id,
            "merchant_order_id": self.name,
            "merchant_status": status or "placed",
        }

    def action_cancel(self):
        for order in self:
            if not order.credit_key_order_id:
                continue
            provider = self.env["payment.provider"].search(
                [("code", "=", "credit_key"),
                ("state", "in", ("test", "enabled"))], limit=1
            )
            if not provider:
                raise UserError(_("No Credit Key payment provider found."))
            ck_order_id = order.credit_key_order_id
            result = provider._credit_key_make_request("find_order", {"id": ck_order_id})
            if result.get("success") is False:
                error_text = result.get("error")
                raise UserError(_("Credit Key request failed: %s") % error_text)
            status = (result.get("status") or "").lower()
            if status == "shipped":
                raise UserError(_("This Credit Key order is already '%s'. Try refunding the order instead.") % status)
            elif status not in ("new", "placed"):
                raise UserError(_("Cannot cancel this Credit Key order because its current status is '%s'.") % status)
            payload = {"id": ck_order_id}
            cancel_res = provider._credit_key_make_request("cancel_order", payload)
            if cancel_res.get("success") is False:
                error_text = cancel_res.get("error")
                raise UserError(_("Credit Key request failed: %s") % error_text)
            if (cancel_res.get("status") or "").lower() != "canceled":
                raise UserError(
                    _("Credit Key failed to cancel this order. Current status: %s") % cancel_res.get("status")
                )
            order.message_post(body=_("Credit Key order %s was successfully canceled.") % ck_order_id)
        return super().action_cancel()

    def _credit_key_check_company(self):
        """Fetch Credit Key company data (moved from wizard)."""
        self.ensure_one()

        print("\n================ CK COMPANY CHECK (MOVE) START ================")
        print("[CK MOVE] Sale Order:", self.name)
        print("[CK MOVE] Partner:", self.partner_id.name)
        print("[CK MOVE] Email:", self.partner_id.email)

        if not self.partner_id.email:
            if not self.partner_id.phone:
                raise UserError(_("Customer must have an email or a phone number."))

        provider = self.env["payment.provider"].search(
            [("code", "=", "credit_key"),
            ("state", "in", ("test", "enabled"))], limit=1
        )

        print("[CK MOVE] Provider:", provider)

        if not provider:
            raise UserError(_("No Credit Key payment provider found."))

        payload = {
            "search": self.partner_id.email or self.partner_id.phone,
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
            if not data:
                return {
                    "type": "ir.actions.act_window",
                    "name": "Credit Key",
                    "res_model": "credit.key.redirect.wizard",
                    "view_mode": "form",
                    "target": "new",
                    "context": {
                        "default_message": _(
                            "<p>No Credit Key company found.</p>"
                            "<a href='https://www.creditkey.com/app/users/sign_in' target='_blank' "
                            "style='display:inline-block; padding:10px 16px; "
                            "background-color:#875A7B; color:white; text-decoration:none; "
                            "border-radius:5px; font-weight:bold;'>Apply Here</a>"
                        )
                    },
                }
        
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
            "default_sale_order_id": self.id,
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
        sale_order = self

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
        for line in self.order_line.filtered(lambda l: l.display_type not in ("line_section", "line_subsection", "line_note")):
            tax_value = float(line.tax_ids and line.price_tax or 0.0)
            print(tax_value, "====================================================")
            item = {
                "merchant_id": str(line.id),
                "name": line.product_id.display_name or line.name or "Item",
                "price": float(line.price_unit),
                "quantity": int(line.product_uom_qty),
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
            "status": "AUTHORIZED",
            "amount": float(self.amount_total),
            "merchant_order_id": self.name or "",
            "shipping_address": shipping_address,
            "billing_address": billing_address,
            "items": cart_items,
            "metadata": {
                "sales_rep": self.user_id.name or self.env.user.name,
            },
        }

    def action_view_credit_key_status(self):
        self.ensure_one()

        status = (self.credit_key_status or "").lower()

        if status in ("placed", "shipped"):
            message = "Order has been successfully placed."
        elif status in ("returned", "canceled") or self.state == "cancel":
            message = "Order has been canceled."
        else:
            message = "Order is in state: %s" % status

        return {
            "type": "ir.actions.act_window",
            "name": "Credit Key Status",
            "res_model": "credit.key.status.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_sale_order_id": self.id,
                "default_credit_key_order_id": self.credit_key_order_id,
                "default_credit_key_status": self.credit_key_status,
                "default_message": message,
            },
        }
