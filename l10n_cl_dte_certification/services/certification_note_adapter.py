# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_note.services.note_issuance_adapter import (
    NoteIssuanceAdapter,
)


class CertificationNoteAdapter(NoteIssuanceAdapter):
    """Adapt assigned SET notes to the shared DTE note issuance pipeline."""

    snapshot_producer_version = "l10n_cl_dte_certification/18.0"
    artifact_producer_version = "l10n_cl_dte_certification/18.0"

    def capture_snapshot(self, move, caf, folio_number, document_code):
        snapshot = super().capture_snapshot(move, caf, folio_number, document_code)
        run_line = move.dte_certification_run_line_id
        set_record = run_line.run_id.set_id
        fiscal_reference = snapshot.get("reference")
        if not fiscal_reference:
            raise ValidationError(_("The certification note has no fiscal reference."))
        snapshot.update(
            {
                "certification": True,
                "sii_environment": "certification",
                "references": [
                    {
                        "line_number": 1,
                        "document_type": "SET",
                        "folio": str(set_record.attention_number),
                        "date": run_line.run_id.emission_date.isoformat(),
                        "reason": f"CASO {run_line.case_identifier}",
                        "reference_code": None,
                    },
                    {
                        "line_number": 2,
                        "document_type": fiscal_reference["document_code"],
                        "folio": str(fiscal_reference["folio"]),
                        "date": fiscal_reference["issue_date"],
                        "reason": fiscal_reference["reason"],
                        "reference_code": fiscal_reference["reference_code"],
                    },
                ],
            }
        )
        if run_line.snapshot.get("note_operation") == "text":
            snapshot["lines"] = [self._text_correction_detail(run_line)]
            snapshot["totals"] = {
                "net": None,
                "exempt": None,
                "vat_rate": None,
                "vat": None,
                "total": "0",
            }
        elif (
            str(document_code) == "56"
            and run_line.snapshot.get("note_operation") == "cancel"
            and run_line.referenced_line_id.snapshot.get("note_operation") == "text"
        ):
            snapshot["lines"] = [
                {
                    "name": _("Cancellation of text-correction note"),
                    "description": fiscal_reference["reason"],
                    "quantity": None,
                    "unit_price": None,
                    "discount_percent": None,
                    "subtotal": "0",
                    "exempt": False,
                    "text_correction": True,
                }
            ]
            snapshot["totals"] = {
                "net": None,
                "exempt": None,
                "vat_rate": None,
                "vat": None,
                "total": "0",
            }
        return snapshot

    @staticmethod
    def _text_correction_detail(run_line):
        content = run_line.snapshot.get("text_correction_content") or []
        values = {item.get("kind"): item.get("value") for item in content}
        if not values.get("dice") or not values.get("debe_decir"):
            raise ValidationError(
                _("The certification text correction has no exact source detail.")
            )
        return {
            "name": _("Text correction"),
            "description": _("Dice: %(old)s. Debe decir: %(new)s.")
            % {"old": values["dice"], "new": values["debe_decir"]},
            "quantity": None,
            "unit_price": None,
            "discount_percent": None,
            "subtotal": "0",
            "exempt": False,
            "text_correction": True,
        }
