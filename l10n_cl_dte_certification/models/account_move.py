# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import UserError

from ..services.certification_note_adapter import CertificationNoteAdapter


class AccountMove(models.Model):
    _inherit = "account.move"

    dte_certification_run_line_id = fields.Many2one(
        "l10n_cl.dte.certification.run.line",
        readonly=True,
        copy=False,
        index=True,
        ondelete="restrict",
    )

    def _get_dte_issuance_adapter(self, document_code):
        self.ensure_one()
        if self.dte_certification_run_line_id and str(document_code) in {"56", "61"}:
            return CertificationNoteAdapter()
        return super()._get_dte_issuance_adapter(document_code)

    def write(self, vals):
        certification_moves = self.filtered("dte_certification_run_line_id")
        allowed_note_link = {
            "dte_note_target_document_id",
            "dte_note_operation",
            "dte_note_reason",
        }
        if certification_moves and set(vals) - allowed_note_link:
            raise UserError(
                _(
                    "Generated certification drafts are immutable. Create a new "
                    "execution to change their fiscal data."
                )
            )
        if certification_moves and any(
            move.dte_certification_run_line_id.document_id
            for move in certification_moves
        ):
            raise UserError(
                _("A certification draft cannot change after its DTE is prepared.")
            )
        return super().write(vals)

    def action_post(self):
        if self.filtered("dte_certification_run_line_id"):
            raise UserError(
                _(
                    "Certification drafts are evidence for SII testing and "
                    "cannot be posted. Use the certification workflow."
                )
            )
        return super().action_post()
