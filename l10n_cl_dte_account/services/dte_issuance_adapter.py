# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.services.invoice_snapshot import InvoiceSnapshotService
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import DteXmlComposer


class DteIssuanceAdapter:
    """Contract for DTE formats handled by the shared issuance lifecycle."""

    supported_document_codes = frozenset()
    artifact_producer_version = "l10n_cl_dte_account/18.0"
    snapshot_producer_version = "l10n_cl_dte_xml/18.0"

    def validate_source(self, move, document_code):
        raise NotImplementedError

    def capture_snapshot(self, move, caf, folio_number, document_code):
        raise NotImplementedError

    def compose_ted(self, snapshot, caf):
        raise NotImplementedError

    def compose_unsigned_document(self, snapshot):
        raise NotImplementedError

    def validate_document(self, xml_bytes):
        raise NotImplementedError


class InvoiceIssuanceAdapter(DteIssuanceAdapter):
    """Adapter for Chilean electronic invoices 33 and 34."""

    supported_document_codes = InvoiceSnapshotService.SUPPORTED_DOCUMENT_CODES

    def __init__(self):
        self.snapshot_service = InvoiceSnapshotService()
        self.xml_composer = DteXmlComposer()

    def validate_source(self, move, document_code):
        return self.snapshot_service.validate_invoice_source(move, document_code)

    def capture_snapshot(self, move, caf, folio_number, document_code):
        return self.snapshot_service.capture_invoice(
            move, caf, folio_number, document_code
        )

    def compose_ted(self, snapshot, caf):
        return self.xml_composer.compose_ted(snapshot, caf)

    def compose_unsigned_document(self, snapshot):
        return self.xml_composer.compose_unsigned_document(snapshot)

    def validate_document(self, xml_bytes):
        return self.xml_composer.validate_document(xml_bytes)

    def sign_document(self, company, environment, unsigned_xml):
        return SignatureService().sign_document(company, environment, unsigned_xml)
