# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services.binding_context import is_native_binding


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    dte_return_origin_line_number = fields.Integer(
        string="Original Fiscal Detail",
        readonly=True,
        copy=False,
    )
    dte_return_map_artifact_id = fields.Many2one(
        "l10n_cl.electronic.artifact",
        string="Original Line Mapping",
        readonly=True,
        copy=False,
        check_company=True,
        ondelete="restrict",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            order = self.env["pos.order"].browse(vals.get("order_id"))
            if order and order._has_fiscal_pos_evidence():
                raise UserError(
                    _("An emitted fiscal order cannot receive new product lines.")
                )
        if not is_native_binding(self.env) and any(
            vals.get("dte_return_origin_line_number")
            or vals.get("dte_return_map_artifact_id")
            for vals in vals_list
        ):
            raise AccessError(
                _("Fiscal return line mapping is assigned only by the server.")
            )
        return super().create(vals_list)

    def write(self, vals):
        target = self.env["pos.order"].browse(vals.get("order_id"))
        if target and target._has_fiscal_pos_evidence():
            raise UserError(
                _("A product line cannot be moved into an emitted fiscal order.")
            )
        if {"dte_return_origin_line_number", "dte_return_map_artifact_id"}.intersection(
            vals
        ) and not is_native_binding(self.env):
            raise AccessError(
                _("Fiscal return line mapping cannot be changed by direct RPC.")
            )
        if {
            "qty",
            "product_id",
            "price_unit",
            "discount",
            "tax_ids",
            "order_id",
            "refunded_orderline_id",
            "dte_return_origin_line_number",
            "dte_return_map_artifact_id",
        }.intersection(vals) and any(
            line.order_id._has_fiscal_pos_evidence() for line in self
        ):
            raise UserError(
                _(
                    "Fiscal POS line economics and identities are immutable "
                    "after issuance."
                )
            )
        return super().write(vals)

    def unlink(self):
        if any(line.order_id._has_fiscal_pos_evidence() for line in self):
            raise UserError(_("A fiscal POS line cannot be deleted."))
        return super().unlink()

    @api.constrains(
        "refunded_orderline_id",
        "dte_return_origin_line_number",
        "dte_return_map_artifact_id",
    )
    def _check_dte_return_lineage(self):
        for line in self.filtered("dte_return_origin_line_number"):
            artifact = line.dte_return_map_artifact_id
            if (
                not line.refunded_orderline_id
                or artifact.role != "pos_line_mapping"
                or artifact.company_id != line.company_id
                or artifact.document_id != line.order_id.dte_return_target_document_id
            ):
                raise ValidationError(
                    _(
                        "The fiscal return line does not match its original "
                        "mapping evidence."
                    )
                )
