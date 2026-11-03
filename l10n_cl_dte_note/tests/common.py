import json
import uuid

from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase


class DteNoteTestCase(DteInvoiceTestCase):
    def create_accepted_source(self, code="33"):
        move = self.make_invoice(code)
        snapshot, _caf = self.capture(move, code=code, folio=730)
        manager_env = self.env["l10n_cl.electronic.document"].with_user(
            self.dte_manager
        )
        document = manager_env.create(
            {
                "name": f"Synthetic accepted source {code}",
                "company_id": self.company.id,
                "sii_environment": "demo",
                "document_code": code,
                "source_key": f"note-source-{uuid.uuid4().hex}",
                "source_move_id": move.id,
            }
        )
        caf_artifact = self.create_artifact(document, "caf_source", b"synthetic CAF")
        document.action_record_transition(
            "authorized", "test_caf_authorized", artifact_id=caf_artifact.id
        )
        document.action_record_transition(
            "numbered", "test_folio_reserved", folio_number=730
        )
        snapshot_bytes = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("UTF-8")
        snapshot_artifact = self.create_artifact(
            document, "source_snapshot", snapshot_bytes
        )
        document.action_record_source_snapshot(snapshot_artifact.id)
        unsigned = self.create_artifact(document, "unsigned_xml", b"synthetic unsigned")
        document.action_record_transition(
            "prepared", "test_xml_prepared", artifact_id=unsigned.id
        )
        signed = self.create_artifact(document, "signed_xml", b"synthetic signed")
        document.action_record_transition(
            "signed", "test_xml_signed", artifact_id=signed.id
        )
        request = self.create_artifact(document, "sii_request", b"synthetic request")
        document.action_record_transition(
            "queued", "test_request_queued", artifact_id=request.id
        )
        response = self.create_artifact(document, "sii_response", b"synthetic accepted")
        document.action_record_transition(
            "submitted", "test_submitted", artifact_id=response.id
        )
        status = self.create_artifact(document, "status_response", b"synthetic status")
        document.action_record_transition(
            "accepted", "test_accepted", artifact_id=status.id
        )
        return move, document, snapshot

    def create_credit_note(self, source_move, target_document, operation="cancel"):
        wizard = (
            self.env["account.move.reversal"]
            .with_user(self.dte_manager)
            .create(
                {
                    "move_ids": [(6, 0, [source_move.id])],
                    "date": source_move.invoice_date,
                    "journal_id": source_move.journal_id.id,
                    "reason": "Corrección sintética",
                    "dte_note_operation": operation,
                }
            )
        )
        wizard.reverse_moves()
        move = wizard.new_move_ids.ensure_one()
        move.with_user(self.dte_manager).write(
            {
                "l10n_latam_document_type_id": self.document_types["61"].id,
                "dte_note_target_document_id": target_document.id,
                "dte_note_operation": operation,
                "dte_note_reason": "Corrección sintética",
            }
        )
        return move
