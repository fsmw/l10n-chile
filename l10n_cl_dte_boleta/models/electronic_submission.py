# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, models

from ..services.boleta_envelope_composer import BoletaEnvelopeComposer
from ..services.boleta_issuance_adapter import BoletaIssuanceAdapter
from ..services.boleta_rest_gateway import BoletaRestGateway
from ..services.boleta_xml_composer import BoletaXmlComposer

REST_ENVIRONMENTS = frozenset({"certification", "production"})


class ElectronicSubmission(models.Model):
    _inherit = "l10n_cl.electronic.submission"

    @api.model
    def _get_document_validator(self, document_code):
        if document_code in BoletaIssuanceAdapter.SUPPORTED_DOCUMENT_CODES:
            return BoletaXmlComposer()
        return super()._get_document_validator(document_code)

    def _is_rest_boleta(self):
        """Boletas reach SII through its REST service, not DTEUpload."""
        self.ensure_one()
        return (
            self.document_id.document_code
            in BoletaIssuanceAdapter.SUPPORTED_DOCUMENT_CODES
            and self.environment in REST_ENVIRONMENTS
        )

    def _gateway(self):
        if self._is_rest_boleta():
            return BoletaRestGateway(self.document_id.document_code)
        return super()._gateway()

    def _uses_envelope_upload(self):
        return not self._is_rest_boleta() and super()._uses_envelope_upload()

    def _prepare_send_request(self, gateway):
        request_bytes = super()._prepare_send_request(gateway)
        if not self._is_rest_boleta():
            return request_bytes
        # Composing before the upload keeps any envelope problem retryable.
        return BoletaEnvelopeComposer().compose(
            self.company_id.sudo(), request_bytes, self.environment
        )
