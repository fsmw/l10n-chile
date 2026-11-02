# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.addons.l10n_cl_dte_account.services.dte_issuance_adapter import (
    DteIssuanceAdapter,
)
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService

from .boleta_snapshot import BoletaSnapshotService
from .boleta_xml_composer import BoletaXmlComposer


class BoletaIssuanceAdapter(DteIssuanceAdapter):
    """Bridge boleta-specific fiscal rules to the shared DTE lifecycle."""

    SUPPORTED_DOCUMENT_CODES = frozenset({"39", "41"})
    supported_document_codes = SUPPORTED_DOCUMENT_CODES
    artifact_producer_version = "l10n_cl_dte_boleta/18.0"
    snapshot_producer_version = "l10n_cl_dte_boleta/18.0"

    def __init__(self):
        self.snapshot_service = BoletaSnapshotService()
        self.xml_composer = BoletaXmlComposer()

    def validate_source(self, move, document_code):
        """Validate source invoice and fiscal data before reserving a folio."""
        return self.snapshot_service.validate_invoice_source(move, document_code)

    def capture_snapshot(self, move, caf_authorization, folio_number, document_code):
        """Capture immutable boleta inputs from a posted customer invoice."""
        return self.snapshot_service.capture_invoice(
            move, caf_authorization, folio_number, document_code
        )

    def compose_ted(self, snapshot, caf_authorization):
        """Compose and CAF-sign the TED for the selected boleta type."""
        return self.xml_composer.compose_ted(snapshot, caf_authorization)

    def compose_unsigned_document(self, snapshot):
        """Serialize the unsigned boleta using the SII boleta format."""
        return self.xml_composer.compose_unsigned_document(snapshot)

    def validate_document(self, xml_bytes):
        """Validate boleta rules, SII schema, TED signature, and XML signature."""
        return self.xml_composer.validate_document(xml_bytes)

    def sign_document(self, company, environment, unsigned_xml):
        return SignatureService().sign_document(company, environment, unsigned_xml)
