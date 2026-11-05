# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.services.invoice_snapshot import InvoiceSnapshotService
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import DteXmlComposer


class DteIssuanceAdapter:
    """Contract for DTE formats handled by the shared issuance lifecycle."""

    supported_document_codes = frozenset()
    artifact_producer_version = "l10n_cl_dte_account/18.0"
    snapshot_producer_version = "l10n_cl_dte_xml/18.0"

    def source_key(self, source, document_code):
        """Return the stable identity for this source and DTE type."""
        if source._name != "account.move":
            raise ValidationError(
                _("The DTE adapter does not support this source model.")
            )
        return f"account.move:{source.id}:type-{document_code}"

    def document_source_values(self, source):
        """Return the source relation values for a new DTE record."""
        if source._name != "account.move":
            raise ValidationError(
                _("The DTE adapter does not support this source model.")
            )
        return {"source_move_id": source.id}

    def select_caf(self, source, environment, document_code):
        """Select and validate the CAF through the source model."""
        return source._select_dte_caf(environment, document_code)

    def create_artifact(self, source, document, role, content, producer_version):
        """Create immutable DTE evidence using the source environment."""
        is_snapshot = role == "source_snapshot"
        return (
            source.env["l10n_cl.electronic.artifact"]
            .sudo()
            .create(
                {
                    "document_id": document.id,
                    "role": role,
                    "content": base64.b64encode(content),
                    "mimetype": "application/json"
                    if is_snapshot
                    else "application/xml",
                    "encoding": "UTF-8" if is_snapshot else "ISO-8859-1",
                    "producer_version": producer_version,
                }
            )
        )

    def validate_source(self, source, document_code):
        raise NotImplementedError

    def capture_snapshot(self, source, caf, folio_number, document_code):
        raise NotImplementedError

    def prepare_document_values(self, source, document_code):
        """Return adapter-owned immutable fields for the new fiscal document."""
        return {}

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
