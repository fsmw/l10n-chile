# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, models

from ..services.liquidation_xml_composer import (
    LIQUIDATION_CODES,
    LiquidationXmlComposer,
)


class ElectronicSubmission(models.Model):
    _inherit = "l10n_cl.electronic.submission"

    @api.model
    def _get_document_validator(self, document_code):
        """Validate settlement invoices with the Liquidacion rules."""
        if document_code in LIQUIDATION_CODES:
            return LiquidationXmlComposer()
        return super()._get_document_validator(document_code)
