# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services.note_reference_service import NoteReferenceService


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    dte_note_origin_line_number = fields.Integer(
        string="Original DTE Line",
        default=0,
        copy=False,
        index=True,
        help=(
            "One-based position in the immutable source snapshot, not an "
            "accounting line ID."
        ),
    )

    def copy_data(self, default=None):
        """Carry fiscal line ordinals through native move reversal copying."""
        values_list = super().copy_data(default=default)
        ordinals = self.env.context.get("dte_note_origin_line_numbers", {})
        if ordinals:
            for line, values in zip(self, values_list, strict=True):
                if line.display_type == "product":
                    ordinal = ordinals.get(str(line.id))
                    if ordinal:
                        values["dte_note_origin_line_number"] = ordinal
        return values_list

    @api.model_create_multi
    def create(self, vals_list):
        move_ids = {vals.get("move_id") for vals in vals_list if vals.get("move_id")}
        protected_moves = (
            self.env["account.move"].browse(move_ids).filtered("dte_document_ids")
        )
        if protected_moves and any(
            vals.get("move_id") in protected_moves.ids for vals in vals_list
        ):
            raise UserError(
                _(
                    "Fiscal accounting details cannot be added after DTE "
                    "evidence exists."
                )
            )
        return super().create(vals_list)

    @api.constrains(
        "dte_note_origin_line_number",
        "move_id",
        "tax_ids",
        "quantity",
        "price_subtotal",
    )
    def _check_dte_note_origin_line(self):
        for line in self.filtered(
            lambda item: (
                item.move_id.dte_note_target_document_id
                and item.display_type == "product"
            )
        ):
            snapshot = NoteReferenceService(self.env).read_original_snapshot(
                line.move_id.dte_note_target_document_id
            )
            ordinal = line.dte_note_origin_line_number
            if line.move_id.state == "posted" and not ordinal:
                raise ValidationError(
                    _(
                        "Every posted DTE note detail must reference an original "
                        "fiscal line."
                    )
                )
            if not ordinal:
                continue
            reference_lines = (
                snapshot.get("adjustment", {}).get("line_effects") or snapshot["lines"]
            )
            line_numbers = {
                int(item.get("origin_line_number", index))
                for index, item in enumerate(reference_lines, start=1)
            }
            if ordinal < 1 or ordinal not in line_numbers:
                raise ValidationError(
                    _(
                        "The original DTE line number is outside the referenced "
                        "snapshot."
                    )
                )
            original = next(
                item
                for index, item in enumerate(reference_lines, start=1)
                if int(item.get("origin_line_number", index)) == ordinal
            )
            exempt = bool(original.get("exempt"))
            taxable = any(tax.amount for tax in line.tax_ids)
            if taxable != (not exempt):
                raise ValidationError(
                    _("The note line tax treatment must match its referenced DTE line.")
                )
            if line.price_subtotal < 0 or line.quantity <= 0:
                raise ValidationError(
                    _("DTE note details require positive quantity and amount.")
                )

    def write(self, vals):
        protected = {
            "name",
            "product_id",
            "quantity",
            "price_unit",
            "discount",
            "tax_ids",
            "account_id",
            "currency_id",
            "debit",
            "credit",
            "balance",
            "amount_currency",
            "tax_repartition_line_id",
            "dte_note_origin_line_number",
            "display_type",
        }
        if protected.intersection(vals) and any(
            line.move_id.dte_document_ids for line in self
        ):
            raise UserError(
                _(
                    "Fiscal accounting details are immutable after DTE evidence "
                    "has been created."
                )
            )
        return super().write(vals)

    def unlink(self):
        if any(line.move_id.dte_document_ids for line in self):
            raise UserError(
                _(
                    "Fiscal accounting details cannot be deleted after DTE "
                    "evidence has been created."
                )
            )
        return super().unlink()
