# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.addons.l10n_cl_dte_account.services.dte_issuance_adapter import (
    InvoiceIssuanceAdapter,
)

from .liquidation_snapshot import LiquidationSnapshotService
from .liquidation_xml_composer import LiquidationXmlComposer


class LiquidationIssuanceAdapter(InvoiceIssuanceAdapter):
    """Issue the settlement invoice 43 from a posted vendor bill.

    Folio, payment form, references, TED layout, signature and submission are
    those of invoices 33 and 34; the snapshot and the Liquidacion payload are
    specific to the 43.
    """

    supported_document_codes = LiquidationSnapshotService.SUPPORTED_DOCUMENT_CODES
    artifact_producer_version = "l10n_cl_dte_liquidation/18.0"
    snapshot_producer_version = "l10n_cl_dte_liquidation/18.0"

    def __init__(self):
        super().__init__()
        self.snapshot_service = LiquidationSnapshotService()
        self.xml_composer = LiquidationXmlComposer()

    def capture_snapshot(self, move, caf, folio_number, document_code):
        snapshot = super().capture_snapshot(move, caf, folio_number, document_code)
        # CdgVendedor names a salesperson; a vendor bill has a buyer instead.
        snapshot["issuer"].pop("seller", None)
        return snapshot

    def compose_unsigned_document(self, snapshot):
        return self.xml_composer.compose_unsigned_document(snapshot)
