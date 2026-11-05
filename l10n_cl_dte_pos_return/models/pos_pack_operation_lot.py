# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, models
from odoo.exceptions import UserError


class PosPackOperationLot(models.Model):
    _inherit = "pos.pack.operation.lot"

    def _check_fiscal_lot_change(self, vals):
        orders = self.pos_order_line_id.order_id
        orders |= (
            self.env["pos.order.line"].browse(vals.get("pos_order_line_id")).order_id
        )
        orders |= self.env["pos.order"].browse(vals.get("order_id"))
        if any(order._has_fiscal_pos_evidence() for order in orders):
            raise UserError(
                _("Fiscal POS lot and serial identities are immutable after issuance.")
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._check_fiscal_lot_change(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._check_fiscal_lot_change(vals)
        return super().write(vals)

    def unlink(self):
        self._check_fiscal_lot_change({})
        return super().unlink()
