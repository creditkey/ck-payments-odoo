from odoo import _, models
from odoo.exceptions import UserError


class AccountPaymentRegister(models.TransientModel):
    _inherit = "account.payment.register"

    def _create_payment_vals_from_wizard(self, batch_result):
        """
        Override base Odoo payment register logic:
        - For Credit Key outbound (refund): call /refund
        Otherwise, fall back to normal behavior.
        """
        self.ensure_one()
        payment_method = self.payment_method_line_id
        provider = self.env["payment.provider"].search([("code", "=", payment_method.code)], limit=1)
        if not provider or provider.code != "credit_key":
            return super()._create_payment_vals_from_wizard(batch_result)
        payment_vals = {
            "date": self.payment_date,
            "amount": self.amount,
            "payment_type": self.payment_type,
            "partner_type": self.partner_type,
            "ref": self.communication,
            "journal_id": self.journal_id.id if self.journal_id else False,
            "company_id": self.company_id.id,
            "currency_id": self.currency_id.id,
            "partner_id": self.partner_id.id,
            "partner_bank_id": self.partner_bank_id.id if self.partner_bank_id else False,
            "payment_method_line_id": self.payment_method_line_id.id if self.payment_method_line_id else False,
            "destination_account_id": self.line_ids[0].account_id.id if self.line_ids else False,
            "write_off_line_vals": [],
        }
        if self.payment_type == "outbound":
            related_moves = self.line_ids.mapped("move_id")
            if not related_moves:
                raise UserError(_("No related invoices found to refund."))
            ck_order_id = related_moves[0].credit_key_order_id
            if not ck_order_id:
                raise UserError(_("No Credit Key Order ID found on the related invoice."))
            payload = {"id": ck_order_id, "amount": float(self.amount)}
            provider_code = self.payment_method_line_id.code
            provider = self.env["payment.provider"].search([("code", "=", provider_code)], limit=1)
            refund_failed = False
            error_message = False
            response = provider._credit_key_make_request("refund", payload)
            if response.get("success") is False:
                refund_failed = True
                error_message = _("Credit Key refund API call failed: %s") % response.get("error")
            if not (response.get("status") == "returned" and response.get("capture_status") == "captured"):
                refund_failed = True
                error_message = _("Refund rejected by Credit Key: %s") % response
            if refund_failed:
                payment_vals["refund_failed"] = True
                payment_vals["refund_message"] = error_message
            else:
                related_moves.message_post(
                    body=_("Credit Key refund successful for %s: %s") % (ck_order_id, self.amount)
                )
        return payment_vals

    def _post_payments(self, to_process, edit_mode=False):
        """Skip posting Credit Key refund-failed payments, but keep them as draft."""
        filtered_to_process = []
        for vals in to_process:
            payment_vals = vals.get("create_vals", {})
            payment = vals.get("payment")
            if payment_vals.get("refund_failed"):
                msg = payment_vals.get("refund_message", _("Unknown Credit Key refund error."))
                payment.message_post(body=_("Credit Key refund failed: %s") % msg)
                payment.write({"state": "draft"})
                continue
            filtered_to_process.append(vals)
        if filtered_to_process:
            return super()._post_payments(filtered_to_process, edit_mode=edit_mode)
        else:
            return self.env["account.payment"]

    def _get_active_invoice(self):
        """Ensure the wizard has one posted invoice, even if opened from move lines."""
        active_model = self.env.context.get("active_model")
        active_ids = self.env.context.get("active_ids", [])
        if not active_model or not active_ids:
            raise UserError(_("This wizard must be opened from an invoice."))
        if active_model == "account.move.line":
            lines = self.env["account.move.line"].browse(active_ids)
            moves = lines.mapped("move_id")
        elif active_model == "account.move":
            moves = self.env["account.move"].browse(active_ids)
        else:
            raise UserError(_("Unsupported model: %s") % active_model)
        if len(moves) != 1:
            raise UserError(_("Credit Key can only be triggered for a single invoice at a time."))
        move = moves[0]
        if move.state != "posted":
            raise UserError(_("Please post the invoice before sending to Credit Key."))
        return move
