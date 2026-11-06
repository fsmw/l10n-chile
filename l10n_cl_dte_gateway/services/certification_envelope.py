# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re

from odoo import _
from odoo.exceptions import UserError, ValidationError

from odoo.addons.l10n_cl_dte_signing.services.certificate_identity import normalize_rut

from .certification_auth import CertificationAuthentication
from .certification_response_parser import parse_xml, soap_payload, text
from .envelope_composer import SiiEnvelopeComposer

UPLOAD_PATH = "/cgi_dte/UPL/DTEUpload"


class CertificationSiiEnvelopeGateway:
    """Upload a signed multi-DTE envelope through SII Certification."""

    def __init__(self, authentication_factory=CertificationAuthentication):
        self.authentication_factory = authentication_factory

    def validate_configuration(self, company, environment):
        if environment != "certification":
            raise UserError(
                _(
                    "No verified multi-DTE SII transport is configured for "
                    "%(environment)s. The envelope has not been sent."
                )
                % {"environment": environment}
            )
        normalize_rut(company.vat)

    def send_envelope(self, company, environment, request_bytes, idempotency_key):
        del (
            idempotency_key
        )  # The SII upload endpoint does not accept an idempotency key.
        self.validate_configuration(company, environment)
        sender_vat = SiiEnvelopeComposer().get_sender_vat(request_bytes)
        sender_number, sender_dv = normalize_rut(sender_vat).split("-")
        company_number, company_dv = normalize_rut(company.vat).split("-")
        authentication = self.authentication_factory()
        try:
            token = authentication.token(company)
            authentication.session.cookies.set(
                "TOKEN", token, domain="maullin.sii.cl", path="/"
            )
            response = authentication.post(
                UPLOAD_PATH,
                upload=True,
                data={
                    "rutSender": sender_number,
                    "dvSender": sender_dv,
                    "rutCompany": company_number,
                    "dvCompany": company_dv,
                },
                files={
                    "archivo": (
                        "EnvioDTE.xml",
                        request_bytes,
                        "application/octet-stream",
                    )
                },
                headers={"Referer": "https://www.sii.cl/"},
            )
            return self._parse_upload_response(response)
        finally:
            authentication.close()

    def query_envelope(self, company, environment, track_id):
        self.validate_configuration(company, environment)
        if not re.fullmatch(r"\d{1,10}", track_id or ""):
            raise ValidationError(
                _("The SII response has a missing or ambiguous %(field)s.")
                % {"field": "TRACKID"}
            )
        company_number, company_dv = normalize_rut(company.vat).split("-")
        authentication = self.authentication_factory()
        try:
            token = authentication.token(company)
            response = authentication.soap(
                "QueryEstUp",
                "getEstUp",
                {
                    "Rut": company_number,
                    "Dv": company_dv,
                    "TrackId": track_id,
                    "Token": token,
                },
            )
            payload = soap_payload(response, "getEstUp")
            result_track_id = text(payload, "TRACKID", required=False)
            if result_track_id and result_track_id != track_id:
                raise ValidationError(
                    _("The SII response has a missing or ambiguous %(field)s.")
                    % {"field": "TRACKID"}
                )
            state = text(payload, "ESTADO")
            mapped_state, error_summary = self._map_query_state(payload, state)
            return {
                "state": mapped_state,
                "raw_response": response,
                "error_summary": error_summary,
                "is_simulated": False,
            }
        finally:
            authentication.close()

    @staticmethod
    def _map_query_state(payload, sii_state):
        if sii_state == "PDR":
            return "processing", False
        if sii_state == "EPR":
            informed = _sum_values(payload, "INFORMADOS")
            accepted = _sum_values(payload, "ACEPTADOS")
            rejected = _sum_values(payload, "RECHAZADOS")
            repairs = _sum_values(payload, "REPAROS")
            if informed < 1 or accepted + rejected + repairs != informed:
                raise ValidationError(
                    _("The SII response has a missing or ambiguous %(field)s.")
                    % {"field": "document totals"}
                )
            if rejected:
                return (
                    "rejected",
                    f"SII processed: {accepted} accepted, "
                    f"{rejected} rejected, {repairs} with repairs",
                )
            return (
                "accepted",
                f"SII processed: {accepted} accepted, {repairs} with repairs",
            )
        if sii_state in {"RSC", "RFR", "RCT"}:
            return "rejected", False
        if sii_state in {"SOK", "CRT", "FOK"}:
            return "processing", False
        return "error", f"SII query status {sii_state}"

    @staticmethod
    def _parse_upload_response(response):
        root = parse_xml(response)
        if root.tag.rsplit("}", 1)[-1] != "RECEPCIONDTE":
            raise ValidationError(_("The SII response is not valid XML."))
        status = text(root, "STATUS")
        if not re.fullmatch(r"\d{1,2}", status):
            raise ValidationError(
                _("The SII response has a missing or ambiguous %(field)s.")
                % {"field": "STATUS"}
            )
        if status != "0":
            return {
                "state": "error",
                "track_id": False,
                "raw_response": response,
                "error_summary": f"SII upload status {status}",
                "is_simulated": False,
            }
        track_id = text(root, "TRACKID")
        if not re.fullmatch(r"\d{1,10}", track_id):
            raise ValidationError(
                _("The SII response has a missing or ambiguous %(field)s.")
                % {"field": "TRACKID"}
            )
        return {
            "state": "submitted",
            "track_id": track_id,
            "raw_response": response,
            "is_simulated": False,
        }


def _sum_values(root, name):
    values = root.xpath(".//*[local-name()=$name]/text()", name=name)
    if not values or any(not value.strip().isdigit() for value in values):
        raise ValidationError(
            _("The SII response has a missing or ambiguous %(field)s.")
            % {"field": name}
        )
    return sum(int(value.strip()) for value in values)
