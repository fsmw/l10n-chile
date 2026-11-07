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
        fiscal_fields = {
            "move_type",
            "company_id",
            "journal_id",
            "partner_id",
            "invoice_date",
            "currency_id",
            "l10n_latam_document_type_id",
            "reversed_entry_id",
            "debit_origin_id",
            "invoice_line_ids",
            "dte_note_target_document_id",
            "dte_note_operation",
            "dte_note_reason",
        }
        if certification_moves and any(
            move.dte_certification_run_line_id.document_id
            and fiscal_fields.intersection(vals)
            for move in certification_moves
        ):
            raise UserError(
                _(
                    "A certification draft cannot change fiscal data after DTE "
                    "preparation."
                )
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
