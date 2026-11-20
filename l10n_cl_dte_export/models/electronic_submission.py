# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, models

from ..services.export_issuance_adapter import ExportIssuanceAdapter
from ..services.export_xml_composer import ExportXmlComposer


class ElectronicSubmission(models.Model):
    _inherit = "l10n_cl.electronic.submission"

    @api.model
    def _get_document_validator(self, document_code):
        """Validate export payloads with the Exportaciones rules."""
        if document_code in ExportIssuanceAdapter.SUPPORTED_DOCUMENT_CODES:
            return ExportXmlComposer()
        return super()._get_document_validator(document_code)
