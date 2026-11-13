# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Offline proof that an SII envelope outcome can be safely corrected."""

import base64
import hashlib

from cryptography import x509

from odoo import _, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.l10n_cl_dte_signing.services.certificate_identity import (
    certificate_sender_rut,
    normalize_rut,
)
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService

from ..services.certification_response_parser import (
    parse_status,
    parse_upload,
    parse_xml,
    soap_payload,
    text,
)


class SiiEnvelopeOutcomeVerification(models.AbstractModel):
    """Verify retained request, upload receipt and status without I/O."""

    _name = "l10n_cl.sii.envelope.outcome.verification"
    _description = "SII Envelope Outcome Verification"

    def _verified_schema_rejection(self):
        """Prove a complete rejection before revising the same envelope."""
        return self._verified_retained_outcome()

    def _verified_terminal_outcome(self):
        """Prove a final result before a separate execution uses new folios."""
        return self._verified_retained_outcome(allow_processed=True)

    def _verified_retained_outcome(self, *, allow_processed=False):
        """Verify the request, upload receipt and retained status without I/O.

        Historical configurations may have used the company RUT as sender.
        The receipt's sender must instead match the holder of the certificate
        embedded in the original, cryptographically verified envelope.
        """
        self.ensure_one()
        self._check_manager()
        allowed_states = {"outcome_unknown", "rejected"}
        if allow_processed:
            allowed_states |= {
                "accepted",
                "accepted_with_observations",
                "partially_accepted",
            }
        if self.state not in allowed_states:
            raise UserError(_("Only a verified envelope rejection can be corrected."))
        self._verify_request()
        request = base64.b64decode(self.with_context(bin_size=False).request_xml)
        SignatureService().verify_envelope_signature(request)
        envelope = parse_xml(request)
        if envelope.tag != "{http://www.sii.cl/SiiDte}EnvioDTE":
            raise ValidationError(
                _("Only a signed EnvioDTE envelope can be transmitted.")
            )
        issuer = text(envelope, "RutEmisor")
        if normalize_rut(issuer) != normalize_rut(self.company_id.vat):
            raise ValidationError(_("The envelope issuer differs from the company."))
        certificates = envelope.xpath(
            "./ds:Signature/ds:KeyInfo/ds:X509Data/ds:X509Certificate/text()",
            namespaces={"ds": "http://www.w3.org/2000/09/xmldsig#"},
        )
        if len(certificates) != 1:
            raise ValidationError(
                _("The original envelope signing identity is ambiguous.")
            )
        certificate = x509.load_der_x509_certificate(
            base64.b64decode("".join(certificates[0].split()), validate=True)
        )
        sender = certificate_sender_rut(certificate)
        attempt = self.attempt_ids[:1].with_context(bin_size=False)
        if (
            not attempt
            or attempt.operation not in {"send", "status"}
            or attempt.state not in {"completed", "unknown"}
            or not attempt.finished_at
            or attempt.request_digest != self.request_digest
            or not attempt.response_xml
        ):
            raise UserError(_("A complete original SII rejection receipt is required."))
        response = base64.b64decode(attempt.response_xml, validate=True)
        if hashlib.sha256(response).hexdigest() != attempt.response_digest:
            raise ValidationError(_("The stored SII response fingerprint changed."))
        if attempt.operation == "status":
            return self._verified_cover_rejection(
                response, issuer, sender, allow_processed=allow_processed
            )
        receipt = parse_xml(response)
        if (
            self.track_id
            or text(receipt, "STATUS") != "7"
            or text(receipt, "TRACKID", required=False)
        ):
            raise UserError(_("Only a verified envelope rejection can be corrected."))
        result = parse_upload(response, issuer, sender)
        if result["state"] != "rejected":
            raise UserError(_("Only a verified envelope rejection can be corrected."))
        return {
            "submission_id": self.id,
            "response_digest": attempt.response_digest,
            "code": "7",
            "sender": sender,
        }

    def _verified_cover_rejection(
        self, response, issuer, sender, *, allow_processed=False
    ):
        """Link a final status to the original successful upload receipt."""
        payload = soap_payload(response, "getEstUp")
        code = text(payload, "ESTADO")
        result = parse_status(response, self.track_id)
        allowed_states = {"rejected"}
        allowed_codes = {"RCT", "EPR"}
        if allow_processed:
            allowed_states |= {
                "accepted",
                "accepted_with_observations",
                "partially_accepted",
            }
            allowed_codes |= {"RFR", "RSC"}
        if (
            self.state not in allowed_states
            or not self.track_id
            or text(payload, "TRACKID") != self.track_id
            or code not in allowed_codes
            or result["state"] != self.state
        ):
            raise UserError(_("Only a verified envelope rejection can be corrected."))
        if code != "EPR":
            if payload.xpath(
                ".//*[local-name()='ACEPTADOS' or local-name()='REPAROS']"
            ):
                raise UserError(
                    _("Only a verified envelope rejection can be corrected.")
                )
        else:
            self._check_document_counts(result["counts"])
        sends = self.attempt_ids.filtered(lambda a: a.operation == "send")
        if len(sends) != 1 or sends.state != "completed" or not sends.finished_at:
            raise UserError(_("A complete original SII rejection receipt is required."))
        original = base64.b64decode(
            sends.with_context(bin_size=False).response_xml, validate=True
        )
        if (
            sends.request_digest != self.request_digest
            or hashlib.sha256(original).hexdigest() != sends.response_digest
        ):
            raise ValidationError(_("The stored SII response fingerprint changed."))
        upload = parse_upload(original, issuer, sender)
        if upload["state"] != "submitted" or upload["track_id"] != self.track_id:
            raise UserError(_("A complete original SII rejection receipt is required."))
        return {
            "submission_id": self.id,
            "response_digest": self.attempt_ids[:1].response_digest,
            "upload_response_digest": sends.response_digest,
            "track_id": self.track_id,
            "code": code,
            "counts": result["counts"],
            "sender": sender,
        }

    def _verify_request(self):
        self.ensure_one()
        content = base64.b64decode(
            self.with_context(bin_size=False).request_xml, validate=True
        )
        if hashlib.sha256(content).hexdigest() != self.request_digest:
            raise ValidationError(_("The stored SII envelope fingerprint changed."))
