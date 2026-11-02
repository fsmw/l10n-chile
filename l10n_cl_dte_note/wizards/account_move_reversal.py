# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from decimal import Decimal

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services.note_reference_service import NoteReferenceService


class AccountMoveReversal(models.TransientModel):
    _inherit = "account.move.reversal"

    dte_note_operation = fields.Selection(
        [
            ("cancel", "Cancel document"),
            ("return", "Return goods"),
            ("amount", "Correct amounts"),
        ],
        string="DTE Note Operation",
        default="cancel",
    )
    dte_note_enabled = fields.Boolean(compute="_compute_dte_note_enabled")

    @api.depends("move_ids")
    def _compute_dte_note_enabled(self):
        for wizard in self:
            wizard.dte_note_enabled = any(
                move.dte_document_ids for move in wizard.move_ids
            )

    def _dte_credit_target(self, move):
        docs = move.dte_document_ids
        if not docs:
            return self.env["l10n_cl.electronic.document"]
        if len(docs) != 1:
            raise ValidationError(
                _("Select one source document with exactly one issued DTE.")
            )
        target = docs.ensure_one()
        if target.fiscal_state != "accepted" or target.sii_environment != "demo":
            raise UserError(_("A DTE credit note requires an accepted Demo source."))
        if target.document_code not in NoteReferenceService.ROOT_DOCUMENT_CODES:
            raise ValidationError(
                _("A DTE credit note cannot reference another credit or debit note.")
            )
        snapshot = NoteReferenceService(move.env).read_original_snapshot(target)
        source_lines = move.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        if len(source_lines) != len(snapshot["lines"]):
            raise ValidationError(
                _("The accounting lines no longer match the immutable DTE snapshot.")
            )
        for line, fiscal_line in zip(source_lines, snapshot["lines"], strict=True):
            if Decimal(str(line.price_subtotal)) != Decimal(
                str(fiscal_line["subtotal"])
            ):
                raise ValidationError(
                    _("The original accounting lines changed after DTE acceptance.")
                )
        return target

    def _prepare_dte_credit_values(self, move):
        """Prepare Chilean type 61 and the immutable reference for the reversal."""
        target = self._dte_credit_target(move)
        if not target:
            return {}
        reason = (self.reason or "").strip()
        if not reason:
            raise UserError(
                _("Enter the reason that will be included in the DTE reference.")
            )
        values = move._prepare_dte_note_values(target, self.dte_note_operation, reason)
        values["l10n_latam_document_type_id"] = move._dte_document_type_for_code(
            "61"
        ).id
        return values

    def _prepare_default_reversal(self, move):
        """Merge note metadata into native accounting reversal defaults."""
        values = super()._prepare_default_reversal(move)
        values.update(self._prepare_dte_credit_values(move))
        return values

    def reverse_moves(self, is_modify=False):
        """Create a native draft credit note bound to snapshot ordinals."""
        if any(move.dte_document_ids for move in self.move_ids):
            if len(self.move_ids) != 1:
                raise UserError(
                    _(
                        "Process one DTE source at a time to keep its fiscal "
                        "reference unambiguous."
                    )
                )
            if is_modify:
                raise UserError(
                    _(
                        "The replacement-invoice workflow is not supported "
                        "for issued DTEs."
                    )
                )
        if len(self.move_ids) != 1 or not self.move_ids.dte_document_ids:
            return super().reverse_moves(is_modify=is_modify)

        source = self.move_ids.ensure_one()
        self._dte_credit_target(source)
        source_lines = source.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        ordinals = {
            str(line.id): ordinal for ordinal, line in enumerate(source_lines, start=1)
        }
        reversal_wizard = self.with_context(dte_note_origin_line_numbers=ordinals)
        result = super(AccountMoveReversal, reversal_wizard).reverse_moves(
            is_modify=is_modify
        )
        credit = reversal_wizard.new_move_ids.ensure_one()
        credit_lines = credit.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        if len(source_lines) != len(credit_lines) or credit_lines.mapped(
            "dte_note_origin_line_number"
        ) != list(range(1, len(source_lines) + 1)):
            raise ValidationError(
                _("The reversed accounting details do not match the DTE snapshot.")
            )
        return result
