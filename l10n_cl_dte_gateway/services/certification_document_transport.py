# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Certification (maullin) transport for individually issued DTEs.

It reuses the Certification-set transport and normalizes its results to the
shapes the single-DTE outcome policy already verifies for Production.
"""

from odoo import _, fields
from odoo.exceptions import AccessError, ValidationError

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService

from .certification_auth import CertificationAuthentication, SiiTransportError
from .certification_response_parser import parse_xml, text
from .certification_transport import CertificationSiiTransport
from .envelope_composer import SiiEnvelopeComposer
from .production_response_parser import (
    TOKEN_REJECTED_CODES,
    UPLOAD_NOT_AUTHENTICATED,
    UPLOAD_NOT_PROCESSED,
    parse_status,
    upload_status_message,
)

CERTIFICATION_RESOLUTION_NUMBER = "0"


class CertificationDocumentAuthentication(CertificationAuthentication):
    """Authenticate queued issuance with the Certification signing profile.

    The submission workflow checks the operator before it enters sudo mode.
    """

    require_manager = False


class CertificationDocumentTransport(CertificationSiiTransport):
    """Upload and query one-DTE EnvioDTE envelopes on the Certification host."""

    def __init__(self, authentication_factory=None, document_validator_getter=None):
        """Accept injectable authentication and per-type document validation."""
        super().__init__(authentication_factory or CertificationDocumentAuthentication)
        self.document_validator_getter = document_validator_getter

    @staticmethod
    def _check_transport_access(company):
        """Mirror Production: internal workflows or DTE users may transmit."""
        if company.env.su or company.env.user.has_group(
            "l10n_cl_dte_core.group_dte_user"
        ):
            return
        raise AccessError(
            _("A DTE user or manager is required for Certification transport.")
        )

    def validate_configuration(self, company, environment):
        """Validate Certification readiness before any external dispatch."""
        self._check_transport_access(company)
        super().validate_configuration(company.sudo(), environment)
        if not company.sudo().dte_sii_resolution_date:
            raise ValidationError(
                _("Configure the SII resolution date for Certification.")
            )
        return True

    def validate_request(self, company, environment, request_bytes):
        """Validate the complete signed EnvioDTE before network access."""
        self.validate_configuration(company, environment)
        company = company.sudo()
        SiiEnvelopeComposer(self.document_validator_getter).validate(
            request_bytes,
            signature_service=SignatureService(),
            environment="certification",
        )
        issuer, sender = super().validate_request(company, environment, request_bytes)
        cover = parse_xml(request_bytes).xpath(
            "./*[local-name()='SetDTE']/*[local-name()='Caratula']"
        )[0]
        if text(cover, "NroResol") != CERTIFICATION_RESOLUTION_NUMBER or text(
            cover, "FchResol"
        ) != fields.Date.to_string(company.dte_sii_resolution_date):
            raise ValidationError(
                _("The envelope resolution differs from Certification configuration.")
            )
        return issuer, sender

    def send_envelope(self, company, environment, request_bytes, idempotency_key):
        """Upload one envelope; keep SII's not-processed answers retryable."""
        self._check_transport_access(company)
        result = super().send_envelope(
            company.sudo(), environment, request_bytes, idempotency_key
        )
        code = result["transport_code"]
        if code in UPLOAD_NOT_PROCESSED:
            if code == UPLOAD_NOT_AUTHENTICATED:
                self._forget_token(company)
            # SII ignored the file, so it may be sent again.
            raise SiiTransportError(
                upload_status_message(code),
                dispatched=False,
                response=result["raw_response"],
                code=f"upload_status_{code}",
            )
        if result["state"] == "rejected":
            # The single-DTE outcome policy represents "no TrackID" as False.
            result = {**result, "track_id": False}
        return result

    def query_envelope(self, company, environment, track_id):
        """Query a TrackID and return the Production-shaped status result."""
        self._check_transport_access(company)
        result = super().query_envelope(company.sudo(), environment, track_id)
        raw = result["raw_response"]
        try:
            normalized = parse_status(raw, track_id)
        except ValidationError as error:
            raise SiiTransportError(str(error), response=raw) from None
        if normalized["transport_code"] in TOKEN_REJECTED_CODES:
            self._forget_token(company)
        return normalized

    def _forget_token(self, company):
        """Drop a cached token that SII reported as invalid or expired."""
        auth = self.authentication_factory()
        try:
            auth.invalidate_token(company)
        finally:
            auth.close()
