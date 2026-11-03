from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_note.services.note_reference_service import (
    NoteReferenceService,
)
from odoo.addons.l10n_cl_dte_note.tests.common import DteNoteTestCase


class TestNoteReference(DteNoteTestCase):
    def test_credit_note_requires_reference(self):
        source, _target, _snapshot = self.create_accepted_source()
        note = self.init_invoice(
            "out_refund",
            partner=source.partner_id,
            invoice_date=source.invoice_date,
            post=False,
            products=[self.product_a],
            taxes=[self.tax_sale_a],
        )
        note.l10n_latam_document_type_id = self.document_types["61"]
        with self.assertRaisesRegex(
            ValidationError, "must reference the accepted electronic document"
        ):
            note.action_post()

    def test_debit_note_requires_reference(self):
        source, _target, _snapshot = self.create_accepted_source()
        note = self.init_invoice(
            "out_invoice",
            partner=source.partner_id,
            invoice_date=source.invoice_date,
            post=False,
            products=[self.product_a],
            taxes=[self.tax_sale_a],
        )
        note.l10n_latam_document_type_id = self.document_types["56"]
        with self.assertRaisesRegex(
            ValidationError, "must reference the accepted electronic document"
        ):
            note.action_post()

    def test_reference_fields_are_validated(self):
        source, target, _snapshot = self.create_accepted_source()
        note = self.create_credit_note(source, target)
        with self.env.cr.savepoint():
            with self.assertRaisesRegex(
                ValidationError, "Only document types 61 and 56"
            ):
                note.write(
                    {"l10n_latam_document_type_id": self.document_types["33"].id}
                )
        reference_service = NoteReferenceService(self.env)
        clean_note = self.create_credit_note(source, target)
        invalid_reference = self.env["account.move"].new(
            clean_note.copy_data()[0]
            | {
                "move_type": "out_refund",
                "l10n_latam_document_type_id": self.document_types["61"].id,
                "dte_note_target_document_id": target.id,
                "dte_note_operation": "cancel",
                "reversed_entry_id": False,
            }
        )
        with self.assertRaisesRegex(
            ValidationError, "must reverse the selected customer document"
        ):
            reference_service.validate_reference(invalid_reference, target)

    def test_source_is_unchanged_when_note_created(self):
        source, target, _snapshot = self.create_accepted_source()
        source_identity = (
            source.name,
            source.state,
            source.amount_total,
            source.invoice_date,
        )
        note = self.create_credit_note(source, target)
        self.assertEqual(
            (source.name, source.state, source.amount_total, source.invoice_date),
            source_identity,
        )
        self.assertEqual(note.move_type, "out_refund")
        self.assertEqual(note.dte_note_target_document_id, target)
        self.assertEqual(note.dte_note_operation, "cancel")
