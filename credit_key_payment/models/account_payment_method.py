from odoo import api, models


class AccountPaymentMethod(models.Model):
    _inherit = "account.payment.method"

    def _get_payment_method_information(self):
        """Extend available payment methods metadata."""
        provider = self.env.ref("credit_key_payment.payment_provider_credit_key", raise_if_not_found=False)
        res = super()._get_payment_method_information()
        res["credit_key"] = {
            "mode": "multi",
            "domain": [("type", "in", ("bank", "cash"))],
            "support_inbound": True,
            "support_outbound": True,
            "sequence": 20,
        }
        return res


class AccountJournalPaymentMethodLine(models.Model):
    _inherit = "account.payment.method.line"

    @api.depends("payment_method_id")
    def _compute_payment_provider_id(self):
        super()._compute_payment_provider_id()

        provider = self.env.ref(
            "credit_key_payment.payment_provider_credit_key",
            raise_if_not_found=False,
        )
        if not provider:
            return

        for line in self:
            if line.payment_method_id and line.payment_method_id.code == "credit_key":
                line.payment_provider_id = provider
