# Copyright 2026 Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Production transport adapters for individual DTEs and EnvioDTE envelopes."""

import base64
import re

from cryptography import x509

from odoo import _, fields
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.l10n_cl_dte_signing.services.certificate_identity import (
    certificate_sender_rut,
    normalize_rut,
)
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService

from .certification_auth import PRODUCTION_BASE_URL, SiiTransportError
from .envelope_composer import SiiEnvelopeComposer
from .production_response_parser import parse_status, parse_upload, parse_xml, text
from .sii_authentication import ProductionSiiAuthentication


class ProductionSiiTransport:
    """Authenticate, upload, and query EnvioDTE envelopes in Production."""

    def __init__(self, authentication_factory=None):
        """Accept an injectable authentication factory for isolated tests."""
        self.authentication_factory = (
            authentication_factory or ProductionSiiAuthentication
        )

    def validate_configuration(self, company, environment):
        """Validate production readiness before any external dispatch."""
        if environment != "production" or company.dte_sii_environment != environment:
            raise UserError(_("This SII transport is restricted to Production mode."))
        if not company.env.su and not (
            company.env.user.has_group("l10n_cl_dte_core.group_dte_manager")
            or company.env.user.has_group("l10n_cl_dte_core.group_dte_user")
        ):
            raise AccessError(
                _("A DTE user or manager is required for Production transport.")
            )
        company = company.sudo()
        if not company.dte_sii_resolution_number or not company.dte_sii_resolution_date:
            raise UserError(
                _("Configure the SII resolution number and date for Production.")
            )
        _issuer = self._rut(company.vat)
        self._rut(company.dte_sii_sender_vat)
        profiles = company.env["l10n_cl.dte.signing.profile"].search(
            [
                ("company_id", "=", company.id),
                ("sii_environment", "=", "production"),
                ("active", "=", True),
            ]
        )
        if len(profiles) != 1 or profiles.is_synthetic:
            raise UserError(
                _("Configure one active, non-synthetic Production signing profile.")
            )
        certificate = profiles.sudo().certificate_id
        if (
            not certificate
            or certificate.company_id != company
            or not certificate.active
            or not certificate.is_valid
            or not certificate.private_key_id
            or certificate.private_key_id.public
        ):
            raise UserError(
                _("Configure a valid Production certificate with its private key.")
            )
        try:
            holder = certificate_sender_rut(
                x509.load_pem_x509_certificate(
                    base64.b64decode(certificate.pem_certificate, validate=True)
                )
            )
        except (ValueError, TypeError) as error:
            raise UserError(
                _("The configured Production certificate is invalid.")
            ) from error
        if normalize_rut(company.dte_sii_sender_vat) != holder:
            raise UserError(
                _("The SII sender RUT must match the signing certificate holder.")
            )
        return True

    def test_authentication(self, company, environment):
        """Verify SII authentication without uploading a fiscal document."""
        if not company.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(
                _("Only a DTE manager can test Production authentication.")
            )
        self.validate_configuration(company, environment)
        auth = self.authentication_factory()
        try:
            auth.token(company)
            return True
        finally:
            auth.close()

    def validate_request(self, company, environment, request_bytes):
        """Validate the signed EnvioDTE payload before network access."""
        self.validate_configuration(company, environment)
        company = company.sudo()
        SiiEnvelopeComposer().validate(
            request_bytes,
            signature_service=SignatureService(),
            environment="production",
        )
        root = parse_xml(request_bytes)
        if root.tag != "{http://www.sii.cl/SiiDte}EnvioDTE":
            raise ValidationError(
                _("Only a signed EnvioDTE envelope can be transmitted.")
            )
        covers = root.xpath("./*[local-name()='SetDTE']/*[local-name()='Caratula']")
        if len(covers) != 1:
            raise ValidationError(_("The SII envelope must have one cover sheet."))
        issuer, sender = text(covers[0], "RutEmisor"), text(covers[0], "RutEnvia")
        if text(covers[0], "NroResol") != str(
            company.dte_sii_resolution_number
        ) or text(covers[0], "FchResol") != fields.Date.to_string(
            company.dte_sii_resolution_date
        ):
            raise ValidationError(
                _("The envelope resolution differs from Production configuration.")
            )
        if normalize_rut(issuer) != normalize_rut(company.vat) or normalize_rut(
            sender
        ) != normalize_rut(company.dte_sii_sender_vat):
            raise ValidationError(
                _(
                    "The envelope issuer or sender differs from "
                    "Production configuration."
                )
            )
        SignatureService().verify_envelope_signature(request_bytes)
        return issuer, sender

    def send_envelope(self, company, environment, request_bytes, idempotency_key):
        """Upload one signed envelope and return its raw SII receipt and TrackID."""
        del idempotency_key  # SII's upload endpoint does not support idempotency keys.
        issuer, sender = self.validate_request(company, environment, request_bytes)
        issuer_number, issuer_dv = self._rut(issuer)
        sender_number, sender_dv = self._rut(sender)
        auth = self.authentication_factory()
        try:
            token = auth.token(company)
            response = auth.post(
                "/cgi_dte/UPL/DTEUpload",
                upload=True,
                data={
                    "rutSender": sender_number,
                    "dvSender": sender_dv,
                    "rutCompany": issuer_number,
                    "dvCompany": issuer_dv,
                },
                files={"archivo": ("EnvioDTE.xml", request_bytes, "text/xml")},
                headers={
                    "Cookie": "TOKEN=" + token,
                    "Referer": PRODUCTION_BASE_URL,
                    "Accept": "application/xml",
                },
            )
            try:
                return parse_upload(response, issuer, sender)
            except ValidationError as error:
                raise SiiTransportError(
                    str(error), dispatched=True, response=response
                ) from None
        finally:
            auth.close()

    def query_envelope(self, company, environment, track_id):
        """Query SII processing status for a previously submitted TrackID."""
        self.validate_configuration(company, environment)
        if not re.fullmatch(r"\d{1,10}", track_id or ""):
            raise ValidationError(_("A valid SII TrackID is required."))
        number, dv = self._rut(company.vat)
        auth = self.authentication_factory()
        try:
            response = auth.soap(
                "QueryEstUp",
                "getEstUp",
                {
                    "Rut": number,
                    "Dv": dv,
                    "TrackId": track_id,
                    "Token": auth.token(company),
                },
            )
            return parse_status(response, track_id)
        except ValidationError as error:
            raise SiiTransportError(
                str(error), response=locals().get("response")
            ) from None
        finally:
            auth.close()

    @staticmethod
    def _rut(value):
        normalized = normalize_rut(value)
        number, verifier = normalized.split("-")
        return number, verifier
