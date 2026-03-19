from odoo import _, http
from odoo.http import request


class CreditKeyController(http.Controller):
    @http.route("/credit_key/return", type="http", auth="public", csrf=False)
    def creditkey_return(self, **kwargs):
        """Handle successful checkout redirection from Credit Key."""
        ck_order_id = kwargs.get("ck_order") or kwargs.get("order_id") or kwargs.get("id")
        if not ck_order_id:
            return request.redirect("/payment/status")

        transaction = (
            request.env["payment.transaction"].sudo().search([("credit_key_order_id", "=", ck_order_id)], limit=1)
        )

        provider = transaction.provider_id
        if not provider:
            return request.redirect("/payment/status")

        payload = {"id": ck_order_id}
        result = provider._credit_key_make_request("complete_checkout", payload)
        if result.get("success") is False:
            return request.redirect("/payment/status")

        success = result.get("success")
        find_payload = {"id": ck_order_id}
        response = provider._credit_key_make_request("find_order", find_payload)
        status = response.get("status")

        if not success:
            transaction._mark_ck_payment_failed(ck_order_id)
            transaction.sale_order_ids.write({"credit_key_status": status})
            return request.redirect("/payment/status")

        transaction.sale_order_ids.write({"credit_key_status": status})
        transaction._finalize_credit_key_payment(ck_order_id)
        return request.redirect("/payment/status")

    @http.route("/credit_key/cancel", type="http", auth="public", csrf=False)
    def creditkey_cancel(self, **kwargs):
        """Handle cancelled checkout redirection from Credit Key."""
        ck_order_id = kwargs.get("ck_order")
        if ck_order_id:
            transaction = (
                request.env["payment.transaction"].sudo().search([("credit_key_order_id", "=", ck_order_id)], limit=1)
            )
            payment = transaction.payment_id
            if payment:
                payment.state = "canceled"
                payment.message_post(body=_("Credit Key checkout was canceled by the user."))
        return request.redirect("/shop/checkout")
