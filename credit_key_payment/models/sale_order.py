from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare


class SaleOrder(models.Model):
    _inherit = "sale.order"

    credit_key_order_id = fields.Char()
    credit_key_status = fields.Char()

    def _create_invoices(self, grouped=False, final=False, date=None):
        invoices = super()._create_invoices(grouped=grouped, final=final, date=date)

        for so in self:
            credit_key_tx = so.transaction_ids.filtered(lambda tx: tx.provider_id.code == "credit_key")[:1]
            if not credit_key_tx:
                continue

            provider = credit_key_tx.provider_id
            ck_order_id = credit_key_tx.credit_key_order_id
            if not ck_order_id:
                continue
            linked_invoices = invoices.filtered(lambda inv: inv.invoice_origin == so.name)

            if linked_invoices:
                find_payload = {"id": ck_order_id}
                find_resp = provider._credit_key_make_request("find_order", payload=find_payload)
                if find_resp.get("success") is False:
                    error_text = find_resp.get("error")
                    raise UserError(_("Credit Key request failed: %s") % error_text)
                current_status = find_resp.get("status", "failed to fetch")
                ck_total = float(find_resp.get("charges", {}).get("grand_total", 0.0))
                difference = round(ck_total - credit_key_tx.amount, 2)
                if difference:
                    credit_key_tx = so._ck_recreate_transaction(credit_key_tx, invoices, ck_total)

                linked_invoices.write({
                    "credit_key_order_id": ck_order_id,
                    "credit_key_status": current_status,
                })
                linked_invoices.message_post(
                    body=_("Linked to Credit Key order %s with CK status '%s'.") % (ck_order_id, current_status)
                )
            deliverable_lines = so.order_line.filtered(lambda l: l.product_id.invoice_policy == "delivery")

            all_delivered = all(line.qty_delivered >= line.product_uom_qty for line in deliverable_lines)

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

    def _ck_recreate_transaction(self, credit_key_tx, invoices, ck_total):
        self.ensure_one()

        provider = credit_key_tx.provider_id
        ck_order_id = credit_key_tx.credit_key_order_id

        old_tx = credit_key_tx
        if float_compare(old_tx.amount, ck_total, precision_digits=2) == 0:
            return old_tx
        old_tx._set_canceled(state_message="Recreating transaction due to update.", extra_allowed_states=("done",))
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
            provider = self.env["payment.provider"].sudo().search([("code", "=", "credit_key")], limit=1)
            if not provider:
                raise UserError(_("No Credit Key provider found."))
            ck_order_id = order.credit_key_order_id
            result = provider._credit_key_make_request("find_order", {"id": ck_order_id})
            if result.get("success") is False:
                raise UserError(_("Credit Key request failed: %s") % result.get("error"))
            status = (result.get("status") or "").lower()
            if status not in ("placed", "new"):
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
            provider = self.env["payment.provider"].sudo().search([("code", "=", "credit_key")], limit=1)
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
