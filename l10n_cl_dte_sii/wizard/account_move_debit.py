# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import UserError


class AccountDebitNote(models.TransientModel):
    _inherit = "account.debit.note"

    l10n_cl_edi_reference_doc_code = fields.Selection(
        [("1", "Anula documento"), ("2", "Corrige texto"), ("3", "Corrige montos")],
        string="Código de referencia SII",
    )
    l10n_cl_original_text = fields.Char(string="Texto original")
    l10n_cl_corrected_text = fields.Char(string="Texto corregido")

    def _prepare_default_values(self, move):
        values = super()._prepare_default_values(move)
        if (
            move.company_id.account_fiscal_country_id.code != "CL"
            or not move.journal_id.l10n_latam_use_documents
            or move.l10n_latam_document_type_id.code not in ("33", "34")
        ):
            return values
        if not self.l10n_cl_edi_reference_doc_code:
            raise UserError(
                _("Seleccione el código de referencia de la nota de débito.")
            )
        if self.l10n_cl_edi_reference_doc_code == "2" and not (
            self.l10n_cl_original_text and self.l10n_cl_corrected_text
        ):
            raise UserError(
                _("Indique el texto original y el texto corregido para continuar.")
            )

        debit_type = self.env["l10n_latam.document.type"].search(
            [
                ("country_id.code", "=", "CL"),
                ("code", "=", "56"),
            ],
            limit=1,
        )
        if not debit_type:
            raise UserError(_("No está configurado el tipo DTE chileno 56."))

        values.update(
            {
                "invoice_origin": _(
                    "%(prefix)s %(number)s",
                    prefix=move.l10n_latam_document_type_id.doc_code_prefix,
                    number=move.l10n_latam_document_number,
                ),
                "l10n_latam_document_type_id": debit_type.id,
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
        if self.l10n_cl_edi_reference_doc_code == "2":
            values["line_ids"] = [
                (5, 0, 0),
                (
                    0,
                    0,
                    {
                        "account_id": move.journal_id.default_account_id.id,
                        "name": _(
                            "Donde dice: %(original)s debe decir: %(corrected)s",
                            original=self.l10n_cl_original_text,
                            corrected=self.l10n_cl_corrected_text,
                        ),
                        "quantity": 1,
                        "price_unit": 0.0,
                    },
                ),
            ]
        return values
