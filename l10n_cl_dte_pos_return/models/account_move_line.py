# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from ..services.binding_context import is_native_binding


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    dte_pos_order_line_id = fields.Many2one(
        "pos.order.line",
        string="POS Fiscal Source Line",
        readonly=True,
        copy=False,
        check_company=True,
        index=True,
        ondelete="restrict",
    )

    @api.model_create_multi
    def create(self, vals_list):
        if not is_native_binding(self.env) and any(
            vals.get("dte_pos_order_line_id") for vals in vals_list
        ):
            raise AccessError(
                _("Native fiscal POS invoice line bindings cannot be forged.")
            )
        return super().create(vals_list)

    def write(self, vals):
        if "dte_pos_order_line_id" in vals and not is_native_binding(self.env):
            raise AccessError(_("The fiscal POS invoice line identity is immutable."))
        return super().write(vals)

    @api.constrains("dte_pos_order_line_id", "move_id")
    def _check_dte_pos_source_line(self):
        for line in self.filtered("dte_pos_order_line_id"):
            source = line.dte_pos_order_line_id
            if (
                source.company_id != line.company_id
                or source.product_id != line.product_id
                or (
                    line.move_id.pos_order_ids
                    and source.order_id not in line.move_id.pos_order_ids
                )
            ):
                raise ValidationError(
                    _(
                        "The invoice detail must belong to its native POS order "
                        "and product."
                    )
                )
