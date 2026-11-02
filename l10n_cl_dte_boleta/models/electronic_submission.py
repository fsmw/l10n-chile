# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models

from ..services.boleta_issuance_adapter import BoletaIssuanceAdapter
from ..services.boleta_xml_composer import BoletaXmlComposer


class ElectronicSubmission(models.Model):
    _inherit = "l10n_cl.electronic.submission"

    def _validate_document_payload(self, request_bytes):
        self.ensure_one()
        if (
            self.document_id.document_code
            in BoletaIssuanceAdapter.SUPPORTED_DOCUMENT_CODES
        ):
            return BoletaXmlComposer().validate_document(request_bytes)
        return super()._validate_document_payload(request_bytes)
