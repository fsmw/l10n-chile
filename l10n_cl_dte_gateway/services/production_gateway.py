import hashlib
import uuid

from odoo import _
from odoo.exceptions import UserError, ValidationError

from .envelope_composer import SiiEnvelopeComposer
from .production_response_parser import parse_xml
from .production_transport import ProductionSiiTransport
from .sii_gateway import SiiGateway

SUPPORTED_PRODUCTION_DTE_CODES = {"33", "52", "56", "61"}


class ProductionSiiGateway(SiiGateway):
    """Implement the durable per-DTE gateway contract using one-DTE envelopes."""

    def __init__(self, transport_factory=None):
        """Accept an injectable production transport factory for isolated tests."""
        self.transport_factory = transport_factory or ProductionSiiTransport

    def validate_configuration(self, company, environment):
        """Reject unready production configuration before the job enters sending."""
        self._transport().validate_configuration(company, environment)
        return True

    def _transport(self):
        return self.transport_factory()

    def send_document(
        self, company, environment, request_bytes, idempotency_key, scenario
    ):
        """Wrap one signed DTE, upload it, and normalize its receipt."""
        envelope = self.prepare_document(company, environment, request_bytes)
        return self.send_envelope(company, environment, envelope, idempotency_key)

    def prepare_document(self, company, environment, request_bytes):
        """Compose exact signed transport bytes without performing network I/O."""
        self.validate_configuration(company, environment)
        company = company.sudo()
        dte = parse_xml(request_bytes)
        if dte.tag != "{http://www.sii.cl/SiiDte}DTE":
            raise ValidationError(_("The production request must be one signed DTE."))
        doc = dte.find("{http://www.sii.cl/SiiDte}Documento")
        header = (
            doc.find("{http://www.sii.cl/SiiDte}Encabezado")
            if doc is not None
            else None
        )
        id_doc = (
            header.find("{http://www.sii.cl/SiiDte}IdDoc")
            if header is not None
            else None
        )
        code = (
            id_doc.findtext("{http://www.sii.cl/SiiDte}TipoDTE")
            if id_doc is not None
            else None
        )
        if code not in SUPPORTED_PRODUCTION_DTE_CODES:
            raise UserError(
                _(
                    "Production DTE transport currently supports document types "
                    "33, 56 and 61."
                )
            )
        envelope = SiiEnvelopeComposer().compose(
            company,
            [request_bytes],
            {
                "id": "SetDTE-" + uuid.uuid4().hex,
                "sender_vat": company.dte_sii_sender_vat,
                "resolution_number": company.dte_sii_resolution_number,
                "resolution_date": company.dte_sii_resolution_date,
            },
            environment="production",
        )
        return envelope

    def send_envelope(self, company, environment, request_bytes, idempotency_key):
        """Send previously retained signed envelope bytes exactly once."""
        self.validate_configuration(company, environment)
        result = self._transport().send_envelope(
            company, environment, request_bytes, idempotency_key
        )
        result["is_simulated"] = False
        result["request_digest"] = hashlib.sha256(request_bytes).hexdigest()
        return result

    def query_status(
        self, company, environment, track_id, scenario, query_number, idempotency_key
    ):
        """Resolve the SII status for the single DTE represented by this submission."""
        result = self._transport().query_envelope(company, environment, track_id)
        counts = result.get("counts", [])
        if len(counts) == 1 and counts[0]["INFORMADOS"] == 1:
            row = counts[0]
            result["state"] = (
                "rejected"
                if row["RECHAZADOS"] == 1
                else "accepted"
                if row["ACEPTADOS"] + row["REPAROS"] == 1
                else "processing"
            )
            if row["REPAROS"]:
                result["error_summary"] = _(
                    "SII accepted this DTE with observations; review the retained "
                    "response evidence."
                )
        elif result.get("state") in {
            "accepted",
            "rejected",
            "accepted_with_observations",
        }:
            result["state"] = "processing"
        result["is_simulated"] = False
        return result

    def reconcile_submission(self, company, environment, identity, scenario):
        """Reconcile a possibly dispatched submission without retransmitting it."""
        track_id = identity.get("track_id")
        if not track_id:
            raise UserError(
                _(
                    "No TrackID was received. Review the SII portal before any "
                    "manual retry; automatic resend is blocked."
                )
            )
        return self.query_status(
            company,
            environment,
            track_id,
            scenario,
            1,
            identity.get("idempotency_key", ""),
        )

    def query_envelope(self, company, environment, track_id):
        """Delegate a TrackID query to the production transport."""
        return self._transport().query_envelope(company, environment, track_id)
