from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_round


_ERROR_MESSAGES = {
    "unauthorized": "Unauthorized. The public_key and/or shared_secret given are invalid.",
    "not found": "Order not Found.",
    "merchant order id is required": "Merchant Order ID is missing.",
}


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    credit_key_order_id = fields.Char("Credit Key Order ID", copy=False, readonly=True)

    # ------------------------------------------------------------
    # Checkout: Rendering Values
    # ------------------------------------------------------------

    def _get_specific_rendering_values(self, processing_values):
        """Override of payment to return Credit Key-specific rendering values."""
        res = super()._get_specific_rendering_values(processing_values)

        if self.provider_code != "credit_key":
            return res

        payload = self._credit_key_prepare_checkout_payload()
        response = self.provider_id._credit_key_make_request("begin_checkout", payload)

        if response.get("success") is False:
            raise UserError(_("Credit Key request failed: %s") % response.get("error"))
        credit_key_order_id = response.get("id")
        checkout_url = response.get("checkout_url")
        self.credit_key_order_id = credit_key_order_id
        self.sale_order_ids.write({"credit_key_order_id": credit_key_order_id})
        if not checkout_url:
            raise UserError(_("Credit Key: Failed to complete checkout."))
        return {"api_url": checkout_url}

    # ------------------------------------------------------------
    # Payload Preparation
    # ------------------------------------------------------------

    def _credit_key_prepare_checkout_payload(self):
        """Prepare the checkout payload for Credit Key /begin_checkout endpoint."""
        self.ensure_one()
        base_url = self.provider_id.get_base_url()
        return_url = f"{base_url}credit_key/return?ck_order=%CKKEY%"
        cancel_url = f"{base_url}credit_key/cancel"
        partner = self.partner_id

        # Build Cart Items
        cart_items = []
        sale_orders = self.sale_order_ids
        for line in sale_orders.mapped("order_line"):
            if line.product_id:
                cart_items.append({
                    "merchant_id": str(line.id),
                    "name": line.product_id.name or "Item",
                    "price": float_round(float(line.price_subtotal), 2),
                    "quantity": int(line.product_uom_qty),
                    "sku": line.product_id.default_code or "",
                    "tax": float_round(float(line.price_tax or 0.0), 2),
                    "size": line.product_template_id.attribute_line_ids.filtered(
                        lambda a: a.attribute_id.name.lower() == "size"
                    ).mapped("value_ids.name")[:1]
                    or "",
                    "color": line.product_template_id.attribute_line_ids.filtered(
                        lambda a: a.attribute_id.name.lower() == "color"
                    ).mapped("value_ids.name")[:1]
                    or "",
                })
        # Billing and Shipping Addresses
        billing_address = {
            "first_name": partner.name.split()[0] if partner.name else "",
            "last_name": partner.name.split()[-1] if partner.name else "",
            "email": partner.email or "",
            "address1": partner.street or "",
            "city": partner.city or "",
            "state": partner.state_id.code or "",
            "zip": partner.zip or "",
            "phone_number": partner.phone or "",
            "company_name": partner.company_name or partner.commercial_company_name or "",
        }
        shipping_partner = sale_orders[:1].partner_shipping_id or partner
        shipping_address = self._credit_key_format_address(shipping_partner)

        # Charges section
        total = sum(cart.get("price", 0) for cart in cart_items)
        shipping = sum(l.price_total for l in sale_orders.mapped("order_line").filtered(lambda l: l.is_delivery))
        tax = sum(cart.get("tax", 0) for cart in cart_items)
        grand_total = total + tax
        charges = {
            "total": float_round(total, 2),
            "shipping": float_round(shipping, 2),
            "tax": float_round(tax, 2),
            "discount": float_round(
                sum(max(0.0, so.currency_id.round(so.amount_undiscounted - so.amount_untaxed)) for so in sale_orders), 2
            ),
            "grand_total": float_round(grand_total, 2),
        }
        return {
            "cart_items": cart_items,
            "billing_address": billing_address,
            "shipping_address": shipping_address,
            "charges": charges,
            "return_url": return_url,
            "cancel_url": cancel_url,
            "mode": "redirect",
            "remote_id": self.reference,
            "remote_customer_id": partner.name,
        }

    # ------------------------------------------------------------
    # Refund Logic
    # ------------------------------------------------------------

    def _create_child_transaction(self, amount, is_refund=False, **custom_create_values):
        """Override to send a refund request to Credit Key."""
        refund_tx = super()._create_child_transaction(amount or self.amount, is_refund=True)

        if self.provider_code != "credit_key":
            return refund_tx
        amount_to_refund = abs(self.amount)
        payload = refund_tx._credit_key_prepare_refund_payload(amount_to_refund)

        response = self.provider_id._credit_key_make_request("refund", payload)

        if response.get("success") is False:
            raise UserError(_("Credit Key refund request failed: %s") % response.get("error"))

        if response.get("status") in ["returned", "canceled"]:
            refund_tx._set_done()
        else:
            raise UserError(_("Credit Key refund request failed: %s") % response)

        return refund_tx

    def _credit_key_prepare_refund_payload(self, amount_to_refund):
        """Prepare payload for refund API call."""
        self.ensure_one()
        return {
            "id": self.source_transaction_id.credit_key_order_id,
            "amount": round(amount_to_refund, 2),
        }

    # -------------------------------------------------------------------------
    # Finalize flow after Credit Key checkout completes
    # -------------------------------------------------------------------------

    def _finalize_credit_key_payment(self, ck_order_id):
        """Mark payment, related invoices, and sale order(s) as approved."""
        self.ensure_one()
        provider = self.provider_id

        if not provider:
            raise UserError(_("Missing provider for Credit Key transaction."))

        self._set_done()

        sale_orders = self.sale_order_ids
        if not sale_orders:
            return

        for sale_order in sale_orders:
            if sale_order.state in ["draft", "sent"]:
                sale_order.sudo().action_confirm()
            deliverable_lines = sale_order.order_line.filtered(
                lambda l: l.product_id and l.product_id.invoice_policy == "delivery"
            )

            if not deliverable_lines:
                so_response = self._call_credit_key_confirm_order(provider, sale_order, ck_order_id)
                find_payload = {"id": ck_order_id}
                response = provider._credit_key_make_request("find_order", find_payload)
                if response.get("success") is False:
                    raise UserError(_("Credit Key request failed: %s") % response.get("error"))
                status = response.get("status")
                sale_order.credit_key_status = status

    def _mark_ck_payment_failed(self, ck_order_id):
        """Handle unsuccessful /complete_checkout result."""
        payment = self.payment_id
        if payment:
            payment.state = "canceled"
            payment.message_post(body=_("Credit Key checkout failed or declined."))

    # -------------------------------------------------------------------------
    # Confirm Order
    # -------------------------------------------------------------------------

    def _call_credit_key_confirm_order(self, provider, sale_order, ck_order_id):
        """Calls Credit Key /confirm_order endpoint for a completed order."""
        self.ensure_one()

        # Build cart_items
        response = {}
        cart_items = []
        for line in sale_order.order_line.filtered(lambda l: not l.display_type):
            tax_amount = line.price_tax if line.tax_ids else 0.0
            cart_items.append({
                "merchant_id": str(line.id),
                "name": line.product_id.display_name or line.name,
                "price": float_round(float(line.price_subtotal), 2),
                "quantity": int(line.product_uom_qty),
                "sku": line.product_id.default_code or "",
                "tax": float_round(float(tax_amount), 2),
            })

        # Compute charges
        total = sale_order.amount_untaxed
        tax = sale_order.amount_tax
        shipping = sum(l.price_total for l in sale_order.order_line.filtered(lambda l: l.is_delivery))
        discount = (
            sale_order.currency_id.round(sale_order.amount_undiscounted - sale_order.amount_untaxed)
            if sale_order.amount_undiscounted
            else 0.0
        )

        charges = {
            "total": float_round(total, 2),
            "shipping": float_round(shipping, 2),
            "tax": float_round(tax, 2),
            "discount_amount": float_round(discount, 2),
            "grand_total": float_round(sale_order.amount_total, 2),
        }

        # Shipping address
        shipping_partner = sale_order.partner_shipping_id or sale_order.partner_id
        shipping_address = self._credit_key_format_address(shipping_partner)

        payload = {
            "cart_items": cart_items,
            "charges": charges,
            "shipping_address": shipping_address,
            "id": ck_order_id,
            "merchant_order_id": sale_order.name,
            "merchant_status": "shipped",
        }
        response = provider._credit_key_make_request("confirm_order", payload)
        if response.get("success") is False:
            error_text = response.get("error", "")
            raw = _ERROR_MESSAGES.get(error_text)
            error_text = _(raw) if raw else error_text
            sale_order.message_post(body=_("Credit Key order confirmation failed: %s") % error_text)
            return response
        sale_order.message_post(body=_("Credit Key order confirmed successfully with CK ID: %s") % ck_order_id)
        return response

    # -------------------------------------------------------------------------
    # SHIPPING BLOCK HELPER
    # -------------------------------------------------------------------------
    def _credit_key_format_address(self, partner):
        """Format a partner's address into a Credit Key address dict.

        :param partner: The partner record to format.
        :return: Address dict.
        :rtype: dict
        """
        name_parts = partner.name.split() if partner.name else []
        return {
            "first_name": name_parts[0] if name_parts else "",
            "last_name": name_parts[-1] if len(name_parts) > 1 else "",
            "company_name": partner.commercial_company_name or partner.company_name or "",
            "email": partner.email or "",
            "address1": partner.street or "",
            "address2": partner.street2 or "",
            "city": partner.city or "",
            "state": partner.state_id.code or partner.state_id.name or "",
            "zip": partner.zip or "",
            "phone_number": partner.phone or "",
        }
