# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from types import SimpleNamespace
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase

from odoo.addons.l10n_cl_dte_note.services.note_reference_service import (
    NoteReferenceService,
)


class _Record(SimpleNamespace):
    _fields = {"dte_certification_run_line_id"}

    def ensure_one(self):
        return self

    def sudo(self):
        return self

    def exists(self):
        return self


class _Records(list):
    def filtered(self, predicate):
        return _Records(record for record in self if predicate(record))

    def __getattr__(self, name):
        if len(self) == 1:
            return getattr(self[0], name)
        raise AttributeError(name)


class TestCertificationNoteReference(TransactionCase):
    def setUp(self):
        super().setUp()
        company = _Record(dte_sii_environment="certification")
        self.run = _Record(company_id=company)
        invoice_move = _Record()
        self.invoice = _Record(
            document_code="33",
            folio=1,
            preparation_state="signed",
            sii_environment="certification",
            company_id=company,
            source_move_id=invoice_move,
            note_root_document_id=False,
        )
        self.credit_move = _Record(
            l10n_latam_document_type_id=_Record(code="61"),
            dte_note_operation="cancel",
            move_type="out_refund",
            reversed_entry_id=invoice_move,
            dte_note_target_document_id=self.invoice,
        )
        self.credit = _Record(
            document_code="61",
            folio=2,
            preparation_state="signed",
            sii_environment="certification",
            company_id=company,
            source_move_id=self.credit_move,
            note_root_document_id=False,
        )
        self.debit_move = _Record(
            l10n_latam_document_type_id=_Record(code="56"),
            dte_note_operation="cancel",
            move_type="out_invoice",
            debit_origin_id=self.credit_move,
        )
        invoice_line = _Record(
            sequence=1,
            selected=True,
            document_id=self.invoice,
            move_id=invoice_move,
            case_id=_Record(case_identifier="set-1"),
        )
        self.credit_line = _Record(
            id=2,
            sequence=2,
            selected=True,
            document_id=self.credit,
            move_id=self.credit_move,
            run_id=self.run,
            case_id=_Record(
                case_identifier="set-2",
                document_code="61",
                note_operation="cancel",
                reference_case_identifier="set-1",
            ),
        )
        debit_line = _Record(
            sequence=3,
            selected=True,
            move_id=self.debit_move,
            run_id=self.run,
            case_id=_Record(
                case_identifier="set-3",
                document_code="56",
                note_operation="cancel",
                reference_case_identifier="set-2",
            ),
        )
        self.credit_move.dte_certification_run_line_id = self.credit_line
        self.debit_move.dte_certification_run_line_id = debit_line
        self.run.line_ids = _Records([invoice_line, self.credit_line, debit_line])
        self.snapshot = {
            "references": [
                {"document_type": "SET", "folio": "1"},
                {"document_type": "33", "folio": "1"},
            ]
        }

    def test_debit_cancellation_resolves_credit_root_from_its_set(self):
        service = NoteReferenceService(self.env)
        with patch.object(
            service, "read_original_snapshot", return_value=self.snapshot
        ):
            root = service.validate_reference(self.debit_move, self.credit)
        self.assertIs(root, self.invoice)
        self.assertFalse(self.credit.note_root_document_id)

    def test_debit_cancellation_rejects_snapshot_reference_mismatch(self):
        self.snapshot["references"][1]["folio"] = "999"
        service = NoteReferenceService(self.env)
        with patch.object(
            service, "read_original_snapshot", return_value=self.snapshot
        ):
            with self.assertRaises(ValidationError):
                service.validate_reference(self.debit_move, self.credit)

    def test_debit_cancellation_rejects_a_credit_from_another_run(self):
        self.credit_move.dte_certification_run_line_id = _Record(id=999)
        service = NoteReferenceService(self.env)
        with patch.object(
            service, "read_original_snapshot", return_value=self.snapshot
        ):
            with self.assertRaises(ValidationError):
                service.validate_reference(self.debit_move, self.credit)

    def test_debit_cancellation_requires_its_original_invoice_to_be_signed(self):
        self.invoice.preparation_state = "draft"
        service = NoteReferenceService(self.env)
        with patch.object(
            service, "read_original_snapshot", return_value=self.snapshot
        ):
            with self.assertRaises(ValidationError):
                service.validate_reference(self.debit_move, self.credit)
