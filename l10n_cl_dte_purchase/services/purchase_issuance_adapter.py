# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.addons.l10n_cl_dte_account.services.dte_issuance_adapter import (
    InvoiceIssuanceAdapter,
)

from .purchase_snapshot import PurchaseInvoiceSnapshotService


class PurchaseInvoiceIssuanceAdapter(InvoiceIssuanceAdapter):
    """Issue the purchase invoice 46 from a posted vendor bill.

    The XML layout, TED, signature and validation are those of invoices 33
    and 34; only the snapshot differs (parties, signs and the retention).
    """

    supported_document_codes = PurchaseInvoiceSnapshotService.SUPPORTED_DOCUMENT_CODES
    artifact_producer_version = "l10n_cl_dte_purchase/18.0"
    snapshot_producer_version = "l10n_cl_dte_purchase/18.0"

    def __init__(self):
        super().__init__()
        self.snapshot_service = PurchaseInvoiceSnapshotService()

    def capture_snapshot(self, move, caf, folio_number, document_code):
        snapshot = super().capture_snapshot(move, caf, folio_number, document_code)
        # CdgVendedor names the issuer's salesperson; the issuer is the buyer.
        snapshot["issuer"].pop("seller", None)
        return snapshot
