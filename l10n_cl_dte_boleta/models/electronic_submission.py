# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, models

from ..services.boleta_issuance_adapter import BoletaIssuanceAdapter
from ..services.boleta_xml_composer import BoletaXmlComposer


class ElectronicSubmission(models.Model):
    _inherit = "l10n_cl.electronic.submission"

    @api.model
    def _get_document_validator(self, document_code):
        if document_code in BoletaIssuanceAdapter.SUPPORTED_DOCUMENT_CODES:
            return BoletaXmlComposer()
        return super()._get_document_validator(document_code)
