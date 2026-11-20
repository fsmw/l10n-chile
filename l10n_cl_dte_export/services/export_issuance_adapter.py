# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.addons.l10n_cl_dte_account.services.dte_issuance_adapter import (
    DteIssuanceAdapter,
)
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService

from .export_reference_service import NOTE_CODES, ExportReferenceService
from .export_snapshot import ExportSnapshotService
from .export_xml_composer import ExportXmlComposer


class ExportIssuanceAdapter(DteIssuanceAdapter):
    """Bridge the export 110 format to the shared DTE lifecycle."""

    SUPPORTED_DOCUMENT_CODES = ExportSnapshotService.SUPPORTED_DOCUMENT_CODES
    supported_document_codes = SUPPORTED_DOCUMENT_CODES
    artifact_producer_version = "l10n_cl_dte_export/18.0"
    snapshot_producer_version = "l10n_cl_dte_export/18.0"

    def __init__(self):
        self.snapshot_service = ExportSnapshotService()
        self.xml_composer = ExportXmlComposer()

    def prepare_document_values(self, move, document_code):
        """Notes record their root export when the fiscal document is created."""
        if str(document_code) in NOTE_CODES:
            root = ExportReferenceService(move.env).root(move)
            return {"export_root_document_id": root.id}
        return {}

    def validate_source(self, move, document_code):
        """Reject an export invoice that cannot become a valid DTE."""
        return self.snapshot_service.validate_source(move, document_code)

    def capture_snapshot(self, move, caf_authorization, folio_number, document_code):
        """Capture the immutable export inputs of a posted invoice."""
        return self.snapshot_service.capture(
            move, caf_authorization, folio_number, document_code
        )

    def compose_ted(self, snapshot, caf_authorization):
        """Compose and CAF-sign the export TED."""
        return self.xml_composer.compose_ted(snapshot, caf_authorization)

    def compose_unsigned_document(self, snapshot):
        """Serialize the unsigned Exportaciones payload."""
        return self.xml_composer.compose_unsigned_document(snapshot)

    def validate_document(self, xml_bytes):
        """Validate schema, totals, TED and XML signature of the export."""
        return self.xml_composer.validate_document(xml_bytes)

    def sign_document(self, company, environment, unsigned_xml):
        return SignatureService().sign_document(company, environment, unsigned_xml)
