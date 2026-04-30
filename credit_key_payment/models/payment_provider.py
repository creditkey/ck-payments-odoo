import requests
from datetime import datetime, timedelta
from odoo import _, api, fields, models
from odoo.tools.urls import urljoin as url_join
from odoo.exceptions import ValidationError


_ERROR_MESSAGES = {
    "unauthorized": "Unauthorized. The public_key and/or shared_secret given are invalid.",
    "not found": "Order not Found.",
    "merchant order id is required": "Merchant Order ID is missing.",
    "Forbidden": "Forbidden resource. You do not have access to this resource.",
    "Unauthorized": "Unauthorized. You do not have permission to access this resource.",
    "Not Found": "Resource not found",
    "Bad Request": "Invalid search param: must be an email, phone number, EIN, or order key",
    "Unprocessable Entity": """Unable to process request due to the following reasons: MAX_LOAN_AMOUNT_EXCEEDED,
        CREDIT_BALANCE_NEGATIVE, EXCEEDS_AVAILABLE_CREDIT, EXCEEDS_XPL_LIMIT, PAYMENT_AMOUNT_UNAFFORDABLE, NOT_APPROVED, 
        COMPANY_ON_HOLD, COMPANY_PAST_DUE, PENDING_APPLICATION, NO_CREDIT_LINE, NO_BORROWER, ERROR, ADDRESS_MISMATCH""",
    "Bad Request Checkout": "company_id should not be empty",
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

    credit_key_client_key = fields.Char(
        string="Client ID",
        help="The Client ID provided to you by Credit Key.",
        required_if_provider="credit_key",
    )
    credit_key_client_secret = fields.Char(
        string="Client Secret",
        required_if_provider="credit_key",
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
        ])
        for provider in providers:
            provider._credit_key_get_v2_token()

    def _credit_key_get_v2_token(self):
        self.ensure_one()
        now = fields.Datetime.now()
        buffer = timedelta(minutes=5)

        if self.credit_key_v2_token and self.credit_key_v2_token_expiry:
            expiry = fields.Datetime.from_string(self.credit_key_v2_token_expiry)
            if now < expiry - buffer:
                return self.credit_key_v2_token

        try:
            url = "https://api.staging.creditkey.com/auth/login"
            payload = {
                "client_id": self.credit_key_client_key,
                "client_secret": self.credit_key_client_secret,
            }
            response = requests.post(
                url,
                json=payload,
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
                            "message": "Credit Key API access error [%s] on endpoint /auth/login: %s" % (
                                e.response.status_code,
                                error_json.get("message", error_text),
                            ),
                            "path": "credit_key",
                            "func": "_credit_key_get_v2_token",
                            "line": 0,
                        })
                except Exception as parse_err:
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
                return "https://api.creditkey.com/v2"
            else:
                return "https://api.staging.creditkey.com/v2"
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

        if api_version == "v2":
            token = self.credit_key_v2_token
            if not token:
                token = self._credit_key_get_v2_token()
                if not token:
                    return {"success": False, "error": _("Failed to obtain Credit Key v2 access token.")}
            headers = headers or {}
            headers.setdefault("accept", "application/json")
            headers.setdefault("content-type", "application/json")
            headers["Authorization"] = f"Bearer {token}"

        api_url = self._credit_key_get_api_url(api_version=api_version)
        url = url_join(api_url, endpoint)
        auth_params = (
            {}
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
            elif endpoint == "company":
                response = requests.get(
                    url,
                    params={
                        "search": payload.get("search"),
                        "page": payload.get("page", 1),
                        "per_page": payload.get("per_page", 25),
                    },
                    headers=headers,
                )
            else:
                response = requests.post(
                    url,
                    params=auth_params if api_version != "v2" else None,
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
                    result = error_json.get("result", "") or error_json.get("error", "")
                    if result == "Bad Request" and endpoint == "checkout":
                        result = "Bad Request Checkout"
                    raw = _ERROR_MESSAGES.get(result)
                    error_text = _(raw) if raw else result or e.response.text
                except Exception as parse_err:
                    error_text = e.response.text or _("Credit Key API error")
            return {
                "success": False,
                "error": error_text,
                "status_code": getattr(e.response, "status_code", None),
                "message": getattr(e.response, "text", None),
            }
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            return {"success": False, "error": _("Unable to reach Credit Key API endpoint.")}
        except Exception as e:
            return {"success": False, "error": str(e)}
