# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from cryptography import x509
from stdnum.cl import rut

from odoo import _, fields
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.l10n_cl_dte_signing.services.certificate_identity import (
    certificate_sender_rut,
    normalize_rut,
)
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService

from .certification_auth import BASE_URL, CertificationAuthentication, SiiTransportError
from .certification_response_parser import parse_status, parse_upload, parse_xml, text
from .envelope_composer import SiiEnvelopeComposer


class CertificationSiiTransport:
    """Non-simulated SII EnvioDTE transport, restricted to Certification."""

    def __init__(self, authentication_factory=None):
        self.authentication_factory = (
            authentication_factory or CertificationAuthentication
        )

    def validate_configuration(self, company, environment):
        self.env = company.env
        if environment != "certification" or company.dte_sii_environment != environment:
            raise UserError(
                _("This SII envelope transport is restricted to Certification.")
            )
        if not company.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(
                _("Only a DTE manager can use the Certification transport.")
            )
        profiles = company.env["l10n_cl.dte.signing.profile"].search(
            [
                ("company_id", "=", company.id),
                ("sii_environment", "=", environment),
                ("active", "=", True),
            ]
        )
        if len(profiles) != 1 or profiles.is_synthetic:
            raise UserError(
                _("Configure one non-synthetic Certification signing profile.")
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
                _("Configure a valid Certification certificate with its private key.")
            )
        self._rut(company.vat)
        self._rut(company.dte_sii_sender_vat)
        holder = certificate_sender_rut(
            x509.load_pem_x509_certificate(
                base64.b64decode(certificate.pem_certificate)
            )
        )
        if normalize_rut(company.dte_sii_sender_vat) != holder:
            raise UserError(
                _("The SII sender RUT must match the signing certificate holder.")
            )
        return True

    @staticmethod
    def _rut(value):
        if not value or not rut.is_valid(value):
            raise ValidationError(
                _("Configure valid Chilean issuer and authorized sender RUTs.")
            )
        value = rut.compact(value).upper().lstrip("0")
        return value[:-1], value[-1]

    def test_authentication(self, company, environment):
        self.validate_configuration(company, environment)
        profile = company.env["l10n_cl.dte.signing.profile"].search(
            [
                ("company_id", "=", company.id),
                ("sii_environment", "=", environment),
                ("active", "=", True),
            ]
        )
        auth = self.authentication_factory()
        try:
            auth.token(company)
            profile._record_sii_authentication(True)
        except UserError:
            profile._record_sii_authentication(False)
            raise
        finally:
            auth.close()
        return True

    def validate_authentication_state(self, company, environment):
        self.validate_configuration(company, environment)
        profile = company.env["l10n_cl.dte.signing.profile"].search(
            [
                ("company_id", "=", company.id),
                ("sii_environment", "=", environment),
                ("active", "=", True),
            ]
        )
        if (
            not profile.sii_auth_verified_until
            or profile.sii_auth_verified_until <= fields.Datetime.now()
            or profile.sii_auth_identity_digest
            != profile._sii_auth_configuration_digest()
        ):
            raise UserError(
                _(
                    "Use Test SII Authentication successfully before validating "
                    "this set. Authentication proof expires after 30 minutes "
                    "or a change of identity."
                )
            )
        return True

    def validate_request(self, company, environment, request_bytes):
        """Validate frozen upload bytes before queueing or external I/O."""
        self.validate_configuration(company, environment)
        SiiEnvelopeComposer().validate_upload_format(request_bytes)
        root = parse_xml(request_bytes)
        if root.tag != "{http://www.sii.cl/SiiDte}EnvioDTE":
            raise ValidationError(
                _("Only a signed EnvioDTE envelope can be transmitted.")
            )
        headers = root.xpath("./*[local-name()='SetDTE']/*[local-name()='Caratula']")
        if len(headers) != 1:
            raise ValidationError(_("The SII envelope must have one cover sheet."))
        issuer, sender = text(headers[0], "RutEmisor"), text(headers[0], "RutEnvia")
        if self._rut(issuer) != self._rut(company.vat) or self._rut(
            sender
        ) != self._rut(company.dte_sii_sender_vat):
            raise ValidationError(
                _(
                    "The envelope issuer or sender differs from "
                    "the transport configuration."
                )
            )
        SignatureService().verify_envelope_signature(request_bytes)
        return issuer, sender

    def send_envelope(self, company, environment, request_bytes, idempotency_key):
        issuer, sender = self.validate_request(company, environment, request_bytes)
        issuer_number, issuer_dv = self._rut(issuer)
        sender_number, sender_dv = self._rut(sender)
        auth = self.authentication_factory()
        try:
            try:
                token = auth.token(company)
            except UserError as error:
                raise SiiTransportError(str(error)) from None
            response = auth.post(
                "/cgi_dte/UPL/DTEUpload",
                upload=True,
                data={
                    "rutSender": sender_number,
                    "dvSender": sender_dv,
                    "rutCompany": issuer_number,
                    "dvCompany": issuer_dv,
                },
                files={"archivo": ("envio.xml", request_bytes, "text/xml")},
                headers={
                    "Cookie": "TOKEN=" + token,
                    "User-Agent": "Mozilla/4.0 (compatible; PROG 1.0)",
                    "Referer": BASE_URL,
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
        self.validate_configuration(company, environment)
        if not track_id or not track_id.isdigit() or int(track_id) <= 0:
            raise ValidationError(_("A valid SII TrackID is required."))
        number, dv = self._rut(company.vat)
        auth = self.authentication_factory()
        try:
            response = auth.soap(
                "QueryEstUp",
                "getEstUp",
                {
                    "RutCompania": number,
                    "DvCompania": dv,
                    "TrackId": track_id,
                    "Token": auth.token(company),
                },
            )
            try:
                return parse_status(response, track_id)
            except ValidationError as error:
                raise SiiTransportError(str(error), response=response) from None
        finally:
            auth.close()
