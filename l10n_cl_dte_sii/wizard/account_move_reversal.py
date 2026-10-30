# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import UserError

_CREDIT_NOTE_TYPE_BY_SOURCE_TYPE = {
    "33": "61",
    "34": "61",
    "110": "112",
}


class AccountMoveReversal(models.TransientModel):
    _inherit = "account.move.reversal"

    l10n_cl_edi_reference_doc_code = fields.Selection(
        [("1", "Anula documento"), ("2", "Corrige texto"), ("3", "Corrige montos")],
        string="Código de referencia SII",
        copy=False,
    )
    l10n_cl_is_text_correction = fields.Boolean(string="Solo corregir texto")
    l10n_cl_original_text = fields.Char(string="Texto original")
    l10n_cl_corrected_text = fields.Char(string="Texto corregido")

    def reverse_moves(self, is_modify=False):
        self.ensure_one()
        self.move_ids._l10n_cl_check_can_reverse_dte()
        if self.company_id.account_fiscal_country_id.code != "CL":
            return super().reverse_moves(is_modify=is_modify)
        if self.move_type != "entry" and not self.reason:
            raise UserError(_("Debe indicar el motivo de la reversa."))
        self.l10n_cl_edi_reference_doc_code = (
            "1" if is_modify else "2" if self.l10n_cl_is_text_correction else "3"
        )
        return super(
            AccountMoveReversal,
            self.with_context(
                default_l10n_cl_edi_reference_doc_code=(
                    self.l10n_cl_edi_reference_doc_code
                ),
                default_l10n_cl_original_text=self.l10n_cl_original_text,
                default_l10n_cl_corrected_text=self.l10n_cl_corrected_text,
            ),
        ).reverse_moves(is_modify=is_modify)

    def _prepare_default_reversal(self, move):
        values = super()._prepare_default_reversal(move)
        if (
            move.company_id.account_fiscal_country_id.code != "CL"
            or not move.journal_id.l10n_latam_use_documents
            or not move.l10n_latam_document_type_id
            or not move.l10n_latam_document_number
        ):
            return values
        values.update(
            {
                "invoice_origin": _(
                    "%(prefix)s %(number)s",
                    prefix=move.l10n_latam_document_type_id.doc_code_prefix,
                    number=move.l10n_latam_document_number,
                ),
                "l10n_cl_reference_ids": [
                    (
                        0,
                        0,
                        {
                            "origin_doc_number": move.l10n_latam_document_number,
                            "l10n_cl_reference_doc_type_id": (
                                move.l10n_latam_document_type_id.id
                            ),
                            "reference_doc_code": self.l10n_cl_edi_reference_doc_code,
                            "reason": self.reason,
                            "date": move.invoice_date,
                        },
                    )
                ],
            }
        )
        return values

    def _compute_documents_info(self):
        super()._compute_documents_info()
        for wizard in self:
            source_move = wizard.move_ids
            if (
                len(source_move) != 1
                or source_move.company_id.account_fiscal_country_id.code != "CL"
            ):
                continue

            source_code = source_move.l10n_latam_document_type_id.code
            target_code = _CREDIT_NOTE_TYPE_BY_SOURCE_TYPE.get(source_code)
            if not target_code:
                continue

            compatible_types = wizard.l10n_latam_available_document_type_ids.filtered(
                lambda document_type, expected_code=target_code: (
                    document_type.code == expected_code
                )
            )
            if not compatible_types:
                raise UserError(
                    _(
                        "No hay una nota de crédito chilena compatible de tipo "
                        "%(target)s para el documento de origen %(source)s.",
                        target=target_code,
                        source=source_code,
                    )
                )

            # This relation drives both the selector domain and its default.
            wizard.l10n_latam_available_document_type_ids = compatible_types
