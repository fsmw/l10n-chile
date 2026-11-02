# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class AccountDebitNote(models.TransientModel):
    _inherit = "account.debit.note"

    dte_note_operation = fields.Selection(
        [("cancel", "Cancel credit note"), ("amount", "Correct amounts")],
        string="DTE Note Operation",
        default="amount",
    )
    dte_note_enabled = fields.Boolean(compute="_compute_dte_note_enabled")

    @api.depends("move_ids")
    def _compute_dte_note_enabled(self):
        for wizard in self:
            wizard.dte_note_enabled = any(
                move.dte_document_ids for move in wizard.move_ids
            )

    @api.model
    def default_get(self, field_list):
        values = super().default_get(field_list)
        active_moves = self.env["account.move"].browse(
            self.env.context.get("active_ids", [])
            if self.env.context.get("active_model") == "account.move"
            else []
        )
        targets = active_moves.mapped("dte_document_ids")
        if (
            len(active_moves) == 1
            and len(targets) == 1
            and targets.document_code == "61"
        ):
            values["dte_note_operation"] = "cancel"
        return values

    def _dte_debit_target(self, move):
        documents = move.dte_document_ids
        if not documents:
            return self.env["l10n_cl.electronic.document"]
        if len(documents) != 1:
            raise ValidationError(
                _("Select one source document with exactly one issued DTE.")
            )
        target = documents.ensure_one()
        if target.fiscal_state != "accepted" or target.sii_environment != "demo":
            raise UserError(_("A DTE debit note requires an accepted Demo source."))
        allowed_operation = (
            target.document_code in {"33", "34"} and self.dte_note_operation == "amount"
        ) or (target.document_code == "61" and self.dte_note_operation == "cancel")
        if not allowed_operation:
            raise ValidationError(
                _("This debit operation is not supported for the referenced DTE type.")
            )
        return target

    def _prepare_dte_debit_values(self, move):
        """Prepare Chilean type 56 and the immutable source reference."""
        target = self._dte_debit_target(move)
        if not target:
            return {}
        reason = (self.reason or "").strip()
        if not reason:
            raise UserError(
                _("Enter the reason that will be included in the DTE reference.")
            )
        values = move._prepare_dte_note_values(target, self.dte_note_operation, reason)
        values["l10n_latam_document_type_id"] = move._dte_document_type_for_code(
            "56"
        ).id
        if target.document_code == "61" and self.dte_note_operation == "cancel":
            vals = []
            for line in move.invoice_line_ids.filtered(
                lambda item: item.display_type == "product"
            ):
                copied = line.copy_data()[0]
                copied.pop("dte_note_origin_line_number", None)
                vals.append(
                    (
                        0,
                        0,
                        {
                            **copied,
                            "dte_note_origin_line_number": (
                                line.dte_note_origin_line_number
                            )
                            or len(vals) + 1,
                        },
                    )
                )
            values["line_ids"] = [(5, 0, 0), *vals]
        else:
            values["line_ids"] = [(5, 0, 0)]
        return values

    def _prepare_default_values(self, move):
        """Preserve native debit defaults and attach fiscal note metadata."""
        values = super()._prepare_default_values(move)
        values.update(self._prepare_dte_debit_values(move))
        return values

    def create_debit(self):
        """Create one native debit draft and bind copied cancellation details."""
        if (
            any(move.dte_document_ids for move in self.move_ids)
            and len(self.move_ids) != 1
        ):
            raise UserError(
                _(
                    "Process one DTE source at a time to keep its fiscal "
                    "reference unambiguous."
                )
            )
        action = super().create_debit()
        if len(self.move_ids) == 1 and self.move_ids.dte_document_ids:
            new_moves = self.env["account.move"]
            if action.get("res_id"):
                new_moves = new_moves.browse(action["res_id"])
            elif action.get("domain"):
                new_moves = new_moves.search(action["domain"])
            if len(new_moves) != 1:
                raise ValidationError(
                    _("The native debit wizard did not create exactly one note draft.")
                )
            target = self.move_ids.dte_document_ids.ensure_one()
            if target.document_code == "61":
                for ordinal, line in enumerate(
                    new_moves.invoice_line_ids.filtered(
                        lambda item: item.display_type == "product"
                    ),
                    start=1,
                ):
                    if not line.dte_note_origin_line_number:
                        line.dte_note_origin_line_number = ordinal
        return action
