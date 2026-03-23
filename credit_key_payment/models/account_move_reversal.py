from odoo import _, models


class AccountMoveReversal(models.TransientModel):
    _inherit = "account.move.reversal"

    def reverse_moves(self, is_modify=False):
        """Override to link Credit Key order ID to reversal (credit note)."""
        action = super().reverse_moves(is_modify=is_modify)

        new_moves = self.new_move_ids
        if not new_moves:
            return action

        for reversal, original in zip(new_moves, self.move_ids, strict=False):
            if original.credit_key_order_id:
                reversal.write({
                    "credit_key_order_id": original.credit_key_order_id,
                })
                reversal.message_post(
                    body=_("Inherited Credit Key Order ID %s from original invoice %s.")
                    % (original.credit_key_order_id, original.name)
                )

        return action
