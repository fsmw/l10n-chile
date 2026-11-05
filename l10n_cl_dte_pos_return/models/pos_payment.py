# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, models
from odoo.exceptions import UserError


class PosPayment(models.Model):
    _inherit = "pos.payment"

    def _check_fiscal_payment_mutation(self):
        if any(payment.pos_order_id._has_fiscal_pos_evidence() for payment in self):
            raise UserError(_("Fiscal POS payments cannot change after issuance."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            order = self.env["pos.order"].browse(vals.get("pos_order_id"))
            if order and order._has_fiscal_pos_evidence():
                raise UserError(
                    _("An emitted fiscal order cannot receive additional payments.")
                )
        return super().create(vals_list)

    def write(self, vals):
        target = self.env["pos.order"].browse(vals.get("pos_order_id"))
        if target and target._has_fiscal_pos_evidence():
            raise UserError(
                _("A payment cannot be moved into an emitted fiscal order.")
            )
        if {"amount", "payment_method_id", "pos_order_id"}.intersection(vals):
            self._check_fiscal_payment_mutation()
        return super().write(vals)

    def unlink(self):
        self._check_fiscal_payment_mutation()
        return super().unlink()
