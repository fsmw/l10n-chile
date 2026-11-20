# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Prove the VAT of an RCV purchase with the signed XML of its received DTE.

The inbox validates the schema, signature and TED of a received DTE and keeps
its SHA-256; the vendor bill binding keeps the totals read from that XML. The
VAT is verified only when the entry is bound to the received DTE of the RCV
document, the stored XML still matches its digest, the signed VAT explains
the RCV residual and the entry books that VAT.
"""

import base64
import binascii
import hashlib

from odoo.addons.l10n_cl_dte_inbox_account.services.common import (
    BINDING,
    canonical_folio,
    decimal,
    vat,
)
from odoo.addons.l10n_cl_dte_rcv.services.rcv_fiscal_evidence import (
    RcvFiscalEvidence,
)

VALID_CHECK = "valid"
VALIDATED_STATE = "validated"
VALIDATION_CHECKS = ("schema_state", "signature_state", "ted_state")
VERIFIED_CODE = "signed_source_vat"


class RcvSourceEvidence:
    """Verify the VAT of a bound vendor bill against its signed source."""

    def __init__(self, env):
        self.env = env

    def verify(self, document, move, residual):
        """Return the evidence of ``move``, or None if no received DTE is bound."""
        binding = self.env[BINDING].sudo().search([("move_id", "=", move.id)], limit=1)
        if not binding:
            return None
        incoming = binding.incoming_document_id
        code, source_vat = self._check(document, move, binding, incoming, residual)
        return {
            "verified": not code,
            "reason_code": code or VERIFIED_CODE,
            "message": self._message(code, source_vat),
            "residual": residual,
            "source_vat": source_vat,
            "incoming_dte_id": incoming.id,
            "xml_sha256": incoming.xml_sha256,
        }

    def _check(self, document, move, binding, incoming, residual):
        """Return ``(failure_code, signed_vat)``; no failure code proves the VAT."""
        if not self._same_identity(document, binding, incoming):
            return "identity_mismatch", False
        if incoming.xml_validation_state != VALIDATED_STATE or any(
            incoming[check] != VALID_CHECK for check in VALIDATION_CHECKS
        ):
            return "source_validation_failed", False
        if not self._digest_matches(binding, incoming):
            return "source_digest_mismatch", False
        totals = (binding.source_snapshot or {}).get("totals", {})
        try:
            signed = {
                key: float(decimal(totals.get(key, 0)))
                for key in ("net", "exempt", "vat", "total")
            }
        except ValueError:
            return "source_vat_mismatch", False
        currency = document.currency_id
        if totals.get("additional_taxes") or any(
            not currency.is_zero(rcv_value - signed_value)
            for rcv_value, signed_value in (
                (residual, signed["vat"]),
                (document.net_amount, signed["net"]),
                (document.exempt_amount, signed["exempt"]),
                (document.total_amount, signed["total"]),
            )
        ):
            return "source_vat_mismatch", signed["vat"]
        booked = RcvFiscalEvidence(self.env).odoo_fiscal_vat(move)
        if booked is None or not currency.is_zero(booked - signed["vat"]):
            return "booked_vat_mismatch", signed["vat"]
        return False, signed["vat"]

    @staticmethod
    def _same_identity(document, binding, incoming):
        return (
            incoming.id == document._find_rcv_incoming_dte().id
            and binding.company_id == document.company_id
            and binding.issuer_vat == vat(document.issuer_vat)
            and binding.document_code == document.document_type_code
            and binding.folio == canonical_folio(document.folio)
        )

    @staticmethod
    def _digest_matches(binding, incoming):
        try:
            content = base64.b64decode(
                incoming.sudo().with_context(bin_size=False).raw_xml, validate=True
            )
        except (ValueError, TypeError, binascii.Error):
            return False
        digest = hashlib.sha256(content).hexdigest()
        return binding.xml_sha256 == incoming.xml_sha256 == digest

    def _message(self, code, source_vat):
        env = self.env
        messages = {
            False: env._("the signed received DTE states a VAT of %s", source_vat),
            "identity_mismatch": env._(
                "the received DTE bound to the entry has another fiscal identity"
            ),
            "source_validation_failed": env._(
                "the received DTE did not pass the schema, signature and TED "
                "validation"
            ),
            "source_digest_mismatch": env._(
                "the stored received DTE does not match its digest"
            ),
            "source_vat_mismatch": env._(
                "the amounts of the signed received DTE do not explain the residual"
            ),
            "booked_vat_mismatch": env._(
                "the entry does not book the VAT of the signed received DTE"
            ),
        }
        return messages[code]
