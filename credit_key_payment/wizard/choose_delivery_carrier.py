from odoo import models


class ChooseDeliveryCarrier(models.TransientModel):
    _inherit = "choose.delivery.carrier"

    def button_confirm(self):
        self = self.with_context(trigger_ck_update=True)
        return super().button_confirm()
