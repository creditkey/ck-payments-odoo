import requests

from odoo import _, api, fields, models
from odoo.tools.urls import urljoin as url_join
from odoo.exceptions import ValidationError


_ERROR_MESSAGES = {
    "unauthorized": "Unauthorized. The public_key and/or shared_secret given are invalid.",
    "not found": "Order not Found.",
    "merchant order id is required": "Merchant Order ID is missing.",
    "Forbidden": "Forbidden resource. You do not have access to this endpoint.",
    "Unauthorized": "Unauthorized. You do not have permission to access this resource.",
}


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    code = fields.Selection(selection_add=[("credit_key", "Credit Key")], ondelete={"credit_key": "set default"})
    credit_key_public_key = fields.Char(
        string="Credit Key Public key",
        help="The public key provided to you by Credit Key. This varies between staging and production environments.",
        required_if_provider="credit_key",
        groups="base.group_system",
    )
    credit_key_shared_secret = fields.Char(
        string="Credit Key Shared Secret",
        required_if_provider="credit_key",
        groups="base.group_system"
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

    credit_key_api_version = fields.Selection(
        selection=[
            ("v1", "Legacy"),
            ("v2", "V2"),
        ],
        string="API Version",
        help="Select the API version you are working with.",
        default="v1",
    )
    credit_key_client_key = fields.Char(
        string="Client ID",
        help="The Client ID provided to you by Credit Key.",
        # required_if_provider="credit_key",
        groups="base.group_system",
    )
    credit_key_client_secret = fields.Char(
        string="Client Secret",
        # required_if_provider="credit_key",
        groups="base.group_system"
    )
    credit_key_v2_token = fields.Char("Auth Token", readonly=True)
    credit_key_v2_token_expiry = fields.Char("Expiry Time", readonly=True)

    def _compute_feature_support_fields(self):
        """Override of `payment` to enable additional features."""
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == "credit_key").update({
            "support_refund": "partial",
        })

    def _credit_key_cron_refresh_v2_token(self):
        """Cron job to fetch the Credit Key v2 auth token.
        """
        providers = self.search([
            ("code", "=", "credit_key"),
            ("state", "in", ("enabled", "test")),
            ("credit_key_api_version", "=", "v2"),
        ])
        for provider in providers:
            provider._credit_key_get_v2_token()

    def _credit_key_get_v2_token(self):
        self.ensure_one()
        now = fields.Datetime.now()
        buffer = timedelta(minutes=5)

        if self.credit_key_v2_token and self.credit_key_v2_token_expiry:
            if now < self.credit_key_v2_token_expiry - buffer:
                return self.credit_key_v2_token

        try:
            response = requests.post(
                self._credit_key_get_api_url(api_version="v2") + "/auth/token",
                json={
                    "client_key": self.credit_key_client_key,
                    "client_secret": self.credit_key_client_secret,
                },
                headers={"accept": "application/json", "content-type": "application/json"},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            token = data.get("access_token")
            created_at = data.get("created_at")
            expires_in = data.get("expires_in")
            expiry = datetime.utcfromtimestamp(created_at) + timedelta(seconds=expires_in)
            self.sudo().write({
                "credit_key_v2_token": token,
                "credit_key_v2_token_expiry": expiry,
            })
            return token
        except requests.exceptions.HTTPError as e:
            error_text = _("Credit Key API error")
            if e.response is not None:
                try:
                    error_json = e.response.json()
                    result = error_json.get("result", "")
                    error_field = error_json.get("error", "")
                    raw = _ERROR_MESSAGES.get(result) or _ERROR_MESSAGES.get(error_field)
                    error_text = _(raw) if raw else error_json.get("message") or result or e.response.text

                    if e.response.status_code in (401, 403):
                        self.env["ir.logging"].sudo().create({
                            "name": "credit.key.payment.provider",
                            "type": "server",
                            "level": "WARNING",
                            "message": "Credit Key API access error [%s] on endpoint %s: %s" % (
                                e.response.status_code,
                                endpoint,
                                error_json.get("message", error_text),
                            ),
                            "path": "credit_key",
                            "func": "_credit_key_make_request",
                            "line": 0,
                        })
                except Exception:
                    error_text = e.response.text or _("Credit Key API error")
            return {
                "success": False,
                "error": error_text,
                "status_code": getattr(e.response, "status_code", None),
            }

    def _credit_key_get_api_url(self, api_version=None):
        """Return the API URL according to the state and api_version.

        :return: The API URL
        :rtype: str
        """
        self.ensure_one()
        if api_version == "v2":
            if self.state == "enabled":
                return "https://www.creditkey.com/app/v2"
            else:
                return "https://staging.creditkey.com/app/v2"
        if self.state == "enabled":
            return "https://www.creditkey.com/app/ecomm"
        else:
            return "https://staging.creditkey.com/app/ecomm/"

    def _credit_key_make_request(self, endpoint, payload=None, headers=None, api_version=None):
        """Make a request to Credit Key API at the specified endpoint.

        Automatically uses GET for /find_order (with query parameters)
        and POST for all other endpoints (with JSON payload).

        :param str endpoint: The endpoint path, e.g. 'refund', 'update_order', 'find_order'.
        :param dict payload: The payload or query parameters.
        :param dict headers: Optional headers.
        :param char api_version: Identifier for the API url.
        :return: JSON-decoded response on success, or error dict on failure.
        :rtype: dict
        """
        self.ensure_one()

        if "find_order" in endpoint and not (payload and payload.get("id")):
            return {"success": False, "error": _("Missing Credit Key order ID for find_order.")}

        api_url = self._credit_key_get_api_url(api_version=api_version)
        url = url_join(api_url, endpoint)
        auth_params = (
            {"client_key": self.credit_key_client_key, "client_secret": self.credit_key_client_secret}
            if api_version == "v2"
            else {"public_key": self.credit_key_public_key, "shared_secret": self.credit_key_shared_secret}
        )
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
