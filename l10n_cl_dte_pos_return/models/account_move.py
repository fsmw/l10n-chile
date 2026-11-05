# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from ..services.binding_context import is_native_binding
from ..services.pos_return_issuance_adapter import PosAccountingNoteIssuanceAdapter
from ..services.pos_sale_issuance_adapter import PosTrackedInvoiceBoletaIssuanceAdapter


class AccountMove(models.Model):
    _inherit = "account.move"

    dte_pos_return_order_id = fields.Many2one(
        "pos.order",
        string="POS Credit Source",
        readonly=True,
        copy=False,
        check_company=True,
        index=True,
        ondelete="restrict",
    )

    _sql_constraints = [
        (
            "dte_pos_return_order_unique",
            "UNIQUE(dte_pos_return_order_id)",
            "A POS return can have only one accounting credit note.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not is_native_binding(self.env) and any(
            vals.get("dte_pos_return_order_id") for vals in vals_list
        ):
            raise AccessError(_("The native POS credit source cannot be forged."))
        return super().create(vals_list)

    def write(self, vals):
        if "dte_pos_return_order_id" in vals and not is_native_binding(self.env):
            raise AccessError(_("The POS credit source is immutable."))
        return super().write(vals)

    @api.constrains(
        "dte_pos_return_order_id", "reversed_entry_id", "move_type", "state"
    )
    def _check_dte_pos_return_binding(self):
        for move in self.filtered("dte_pos_return_order_id"):
            order = move.dte_pos_return_order_id
            if (
                move.company_id != order.company_id
                or move.move_type != "out_refund"
                or order.dte_return_accounting_mode != "per_order_invoice"
                or move.reversed_entry_id
                != order.dte_return_target_document_id.source_move_id
            ):
                raise ValidationError(
                    _("The accounting credit must reverse the original POS invoice.")
                )

    def _get_dte_issuance_adapter(self, document_code):
        if str(document_code) == "61" and self.dte_pos_return_order_id:
            return PosAccountingNoteIssuanceAdapter()
        if (
            str(document_code) in {"39", "41"}
            and len(self.pos_order_ids) == 1
            and self.pos_order_ids.session_id.dte_pos_returns_enabled
        ):
            return PosTrackedInvoiceBoletaIssuanceAdapter()
        return super()._get_dte_issuance_adapter(document_code)
