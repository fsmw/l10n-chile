from odoo.addons.l10n_cl_dte_note.services.note_snapshot import NoteSnapshotService
from odoo.addons.l10n_cl_dte_note.services.note_xml_composer import NoteXmlComposer
from odoo.addons.l10n_cl_dte_note.tests.common import DteNoteTestCase


class TestNoteXmlAndWizards(DteNoteTestCase):
    def _credit_snapshot(self):
        source, target, _source_snapshot = self.create_accepted_source()
        caf = self.validate_authorization(
            document_code="61", folio_start=760, folio_end=765
        )
        note = self.create_credit_note(source, target)
        note.action_post()
        snapshot = NoteSnapshotService().capture_note(note, caf, 760, "61")
        snapshot["ted_xml"] = NoteXmlComposer().compose_ted(snapshot, caf)
        return source, target, note, caf, snapshot

    def test_type_61_xml_has_reference_reason(self):
        _source, _target, _note, _caf, snapshot = self._credit_snapshot()
        xml_bytes = NoteXmlComposer().compose_unsigned_document(snapshot)
        self.assertIn(b"<TipoDTE>61</TipoDTE>", xml_bytes)
        self.assertIn(b"<RazonRef>Correcci", xml_bytes)
        self.assertIn(b"<TpoDocRef>33</TpoDocRef>", xml_bytes)

    def test_type_56_xml_has_reference_reason(self):
        _source, _target, _note, _caf, snapshot = self._credit_snapshot()
        snapshot["document_code"] = "56"
        snapshot["folio"] = 761
        snapshot["reference"]["reference_code"] = "3"
        snapshot["reference"]["reason"] = "Aumento de monto"
        xml_bytes = NoteXmlComposer().compose_unsigned_document(snapshot)
        self.assertIn(b"<TipoDTE>56</TipoDTE>", xml_bytes)
        self.assertIn(b"<RazonRef>Aumento de monto</RazonRef>", xml_bytes)

    def test_credit_wizard_creates_note(self):
        source, target, _snapshot = self.create_accepted_source()
        wizard = (
            self.env["account.move.reversal"]
            .with_user(self.dte_manager)
            .create(
                {
                    "move_ids": [(6, 0, [source.id])],
                    "date": source.invoice_date,
                    "journal_id": source.journal_id.id,
                    "reason": "Anulación documentada",
                    "dte_note_operation": "cancel",
                }
            )
        )
        wizard.reverse_moves()
        credit = wizard.new_move_ids.ensure_one()
        self.assertEqual(credit.l10n_latam_document_type_id.code, "61")
        self.assertEqual(credit.dte_note_target_document_id, target)
        self.assertEqual(credit.dte_note_reason, "Anulación documentada")
        self.assertEqual(credit.move_type, "out_refund")

    def test_debit_wizard_creates_type_56(self):
        source, target, _snapshot = self.create_accepted_source()
        wizard = (
            self.env["account.debit.note"]
            .with_user(self.dte_manager)
            .create(
                {
                    "move_ids": [(6, 0, [source.id])],
                    "reason": "Ajuste documentado",
                    "dte_note_operation": "amount",
                }
            )
        )
        values = wizard._prepare_dte_debit_values(source)
        self.assertEqual(
            values["l10n_latam_document_type_id"], self.document_types["56"].id
        )
        self.assertEqual(values["dte_note_target_document_id"], target.id)
        self.assertEqual(values["dte_note_operation"], "amount")

    def test_note_demo_flow_keeps_response_evidence(self):
        source, target, _snapshot = self.create_accepted_source()
        self.validate_authorization(document_code="61", folio_start=770, folio_end=775)
        self.create_signing_profile()
        note = self.create_credit_note(source, target)
        note.action_post()
        issued = note.with_user(self.dte_manager).issue_dte()
        self.assertEqual(issued.document_code, "61")
        self.assertEqual(issued.preparation_state, "queued")
        self.assertTrue(issued.payload_digest)
        submission = self.env["l10n_cl.electronic.submission"].search(
            [("document_id", "=", issued.id)], limit=1
        )
        submission._job_send_to_sii()
        submission.invalidate_recordset()
        self.assertEqual(submission.state, "submitted")
        self.assertTrue(submission.track_id)
        self.assertTrue(
            issued.artifact_ids.filtered(lambda item: item.role == "sii_response")
        )
