from odoo import _, fields, models
from odoo.exceptions import UserError


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
