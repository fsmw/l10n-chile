# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError
from odoo.tools.translate import LazyTranslate

from odoo.addons.l10n_cl_dte_gateway.services.boleta_rest import SiiBoletaRestClient
from odoo.addons.l10n_cl_dte_gateway.services.sii_gateway import SiiGateway

_lt = LazyTranslate(__name__, default_lang="en_US")


class BoletaRestGateway(SiiGateway):
    """Send an already composed EnvioBOLETA and read its REST status."""

    def __init__(self, document_code, client_factory=None):
        self.document_code = document_code
        self.client_factory = client_factory or SiiBoletaRestClient

    def validate_configuration(self, company, environment):
        return True

    def send_document(
        self, company, environment, request_bytes, idempotency_key, scenario
    ):
        result = self.client_factory(environment).send_envelope(
            company, request_bytes, company.sudo().dte_sii_sender_vat
        )
        result["is_simulated"] = False
        return result

    def query_status(
        self, company, environment, track_id, scenario, query_number, idempotency_key
    ):
        result = self.client_factory(environment).query(
            company, track_id, self.document_code
        )
        result["is_simulated"] = False
        return result

    def reconcile_submission(self, company, environment, identity, scenario):
        raise UserError(
            _lt(
                "No TrackID was received for this boleta. Check the SII portal "
                "before any manual retry; automatic resend is blocked."
            )
        )
