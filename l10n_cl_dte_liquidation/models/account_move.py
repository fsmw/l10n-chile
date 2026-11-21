# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models

from ..services.liquidation_issuance_adapter import LiquidationIssuanceAdapter


class AccountMove(models.Model):
    _inherit = "account.move"

    def _get_dte_issuance_adapter(self, document_code):
        """Issue settlement invoices 43 through the liquidation adapter."""
        self.ensure_one()
        if str(document_code) in LiquidationIssuanceAdapter.supported_document_codes:
            return LiquidationIssuanceAdapter()
        return super()._get_dte_issuance_adapter(document_code)

    def _get_dte_allowed_move_types(self, document_code):
        """The commission agent issues the 43 from its principal's vendor bill."""
        if str(document_code) in LiquidationIssuanceAdapter.supported_document_codes:
            return ("in_invoice",)
        return super()._get_dte_allowed_move_types(document_code)
