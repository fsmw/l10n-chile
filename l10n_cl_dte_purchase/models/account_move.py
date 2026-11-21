# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models

from ..services.purchase_issuance_adapter import PurchaseInvoiceIssuanceAdapter


class AccountMove(models.Model):
    _inherit = "account.move"

    def _get_dte_issuance_adapter(self, document_code):
        """Issue purchase invoices 46 through the purchase adapter."""
        self.ensure_one()
        if (
            str(document_code)
            in PurchaseInvoiceIssuanceAdapter.supported_document_codes
        ):
            return PurchaseInvoiceIssuanceAdapter()
        return super()._get_dte_issuance_adapter(document_code)

    def _get_dte_allowed_move_types(self, document_code):
        """The buyer issues the 46 from its vendor bill."""
        if (
            str(document_code)
            in PurchaseInvoiceIssuanceAdapter.supported_document_codes
        ):
            return ("in_invoice",)
        return super()._get_dte_allowed_move_types(document_code)
