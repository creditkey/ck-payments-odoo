import requests
from werkzeug.urls import url_join

from odoo import _, fields, models


_ERROR_MESSAGES = {
    "unauthorized": "Unauthorized. The public_key and/or shared_secret given are invalid.",
    "not found": "Order not Found.",
    "merchant order id is required": "Merchant Order ID is missing.",
}


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    code = fields.Selection(selection_add=[("credit_key", "Credit Key")], ondelete={"credit_key": "set default"})
    credit_key_public_key = fields.Char(
        string="Credit Key Public key",
        help="The public key provided to you by Credit Key. This varies between staging and production environments.",
        required_if_provider="credit_key",
    )
    credit_key_shared_secret = fields.Char(
        string="Credit Key Shared Secret",
        required_if_provider="credit_key",
    )
    show_promotional_message_product = fields.Boolean(
        string="Show Credit Key Widget on Website",
        help="If enabled, the Credit Key promotional widget will appear "
        "on product page when this provider is active.",
    )

    show_promotional_message_cart = fields.Boolean(
        string="Show Credit Key Widget on Website",
        help="If enabled, the Credit Key promotional widget will appear "
        "on checkout page when this provider is active.",
    )

    custom_location_selector_product = fields.Char(
        string="Custom Selector (Product)", help="Enter the custom price tag selector."
    )

    custom_location_selector_cart = fields.Char(
        string="Custom Selector (Checkout)", help="Enter the custom price tag selector."
    )

    def _compute_feature_support_fields(self):
        """Override of `payment` to enable additional features."""
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == "credit_key").update({
            "support_refund": "partial",
        })

    def _credit_key_get_api_url(self):
        """Return the API URL according to the state.

        Note: self.ensure_one()

        :return: The API URL
        :rtype: str
        """
        self.ensure_one()
        if self.state == "enabled":
            return "https://www.creditkey.com/app/ecomm/"
        else:
            return "https://staging.creditkey.com/app/ecomm/"

    def _credit_key_make_request(self, endpoint, payload=None, headers=None):
        """Make a request to Credit Key API.

        :param str endpoint: The endpoint path, e.g. 'refund', 'update_order', 'find_order'.
        :param dict payload: The payload or query parameters.
        :param dict headers: Optional headers.
        :return: JSON-decoded response on success, or error dict on failure.
        :rtype: dict
        """
        self.ensure_one()

        if "find_order" in endpoint and not (payload and payload.get("id")):
            return {"success": False, "error": _("Missing Credit Key order ID for find_order.")}

        api_url = self._credit_key_get_api_url()
        url = url_join(api_url, endpoint)
        auth_params = {
            "public_key": self.credit_key_public_key,
            "shared_secret": self.credit_key_shared_secret,
        }
        headers = headers or {"accept": "application/json", "content-type": "application/json"}

        try:
            if "find_order" in endpoint:
                response = requests.get(
                    url,
                    params={**auth_params, "id": payload["id"]},
                    headers={"accept": "application/json"},
                    timeout=30,
                )
            else:
                response = requests.post(
                    url,
                    params=auth_params,
                    json=payload or {},
                    headers=headers,
                    timeout=30,
                )
            response.raise_for_status()
            return response.json()

        except requests.exceptions.HTTPError as e:
            error_text = _("Credit Key API error")
            if e.response is not None:
                try:
                    error_json = e.response.json()
                    result = error_json.get("result", "")
                    raw = _ERROR_MESSAGES.get(result)
                    error_text = _(raw) if raw else error_json.get("message") or result or e.response.text
                except Exception:
                    error_text = e.response.text or _("Credit Key API error")
            return {
                "success": False,
                "error": error_text,
                "status_code": getattr(e.response, "status_code", None),
            }
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            return {"success": False, "error": _("Unable to reach Credit Key API endpoint.")}
        except Exception as e:
            return {"success": False, "error": str(e)}
