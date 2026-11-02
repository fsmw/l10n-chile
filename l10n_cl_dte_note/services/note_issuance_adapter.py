# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.addons.l10n_cl_dte_account.services.dte_issuance_adapter import (
    DteIssuanceAdapter,
)
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService

from .note_reference_service import NoteReferenceService
from .note_snapshot import NoteSnapshotService
from .note_xml_composer import NoteXmlComposer


class NoteIssuanceAdapter(DteIssuanceAdapter):
    """Complete the shared durable issuance contract for Chilean DTE notes."""

    supported_document_codes = frozenset({"61", "56"})
    snapshot_producer_version = "l10n_cl_dte_note/18.0"
    artifact_producer_version = "l10n_cl_dte_note/18.0"

    def __init__(self):
        self.snapshot_service = NoteSnapshotService()
        self.xml_composer = NoteXmlComposer()

    def validate_source(self, move, document_code):
        """Validate the adjustment under a root lock before reserving its folio."""
        self.snapshot_service.validate_note_source(move, document_code)
        move.dte_note_target_document_id._lock_note_chain(
            move.dte_note_target_document_id
        )
        source_key = f"account.move:{move.id}:type-{document_code}"
        move.invalidate_recordset(["dte_document_ids"])
        existing = move.dte_document_ids.filtered(
            lambda document: document.source_key == source_key
        )
        if existing:
            return
        self.snapshot_service.validate_note_source(move, document_code)

    def prepare_document_values(self, move, document_code):
        """Set immutable fiscal target/root links when the evidence row is created."""
        root = NoteReferenceService(move.env).validate_reference(
            move, move.dte_note_target_document_id
        )
        return {
            "note_target_document_id": move.dte_note_target_document_id.id,
            "note_root_document_id": root.id,
        }

    def capture_snapshot(self, move, caf, folio_number, document_code):
        return self.snapshot_service.capture_note(
            move, caf, folio_number, document_code
        )

    def compose_ted(self, snapshot, caf):
        return self.xml_composer.compose_ted(snapshot, caf)

    def compose_unsigned_document(self, snapshot):
        return self.xml_composer.compose_unsigned_document(snapshot)

    def sign_document(self, company, environment, unsigned_xml):
        return SignatureService().sign_document(company, environment, unsigned_xml)

    def validate_document(self, xml_bytes):
        return self.xml_composer.validate_document(xml_bytes)
