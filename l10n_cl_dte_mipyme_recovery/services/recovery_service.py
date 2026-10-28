# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Orchestration independent from the Odoo transient wizard."""

from dataclasses import dataclass
from datetime import date

from .identity import DteIdentity, normalize_dte_type, normalize_folio, normalize_rut
from .mipyme_adapter import MipymeAdapterError
from .xml_dte import DteCandidate, extract_candidates, validate_candidate

RECOVERY_STATUSES = {
    "recovered",
    "not_found",
    "multiple_matches",
    "invalid_xml",
    "captcha",
    "authentication_failed",
    "transport_error",
    "indeterminate",
    "rejected",
    "production_only",
    "invoice_missing",
    "invoice_ambiguous",
    "review",
}

ERROR_STATUS = {
    "RESPALDO_CAPTCHA": "captcha",
    "RESPALDO_SIN_DATOS": "not_found",
    "RESPALDO_INDETERMINADO": "indeterminate",
    "RESPALDO_RECHAZADO": "rejected",
    "RESPALDO_SIN_EMPRESA": "authentication_failed",
    "SII_AUTHENTICATION_FAILED": "authentication_failed",
    "SII_TRANSPORT_ERROR": "transport_error",
    "MIPYME_PRODUCTION_ONLY": "production_only",
}


@dataclass(frozen=True)
class RecoveryResult:
    status: str
    identity: DteIdentity
    candidate: DteCandidate = None
    digest: str = ""
    error_code: str = ""
    diagnostic: str = ""
    chunks: int = 0


class RecoveryService:
    """Validate portal output before handing bytes to durable persistence."""

    def __init__(self, adapter):
        self.adapter = adapter

    def recover(self, company, issuer_rut, dte_type, folio, date_from, date_to):
        identity = DteIdentity(
            normalize_rut(issuer_rut),
            normalize_dte_type(dte_type),
            normalize_folio(folio),
        )
        self._validate_dates(date_from, date_to)
        try:
            chunks = self.adapter.retrieve(
                company,
                identity.issuer_rut,
                identity.dte_type,
                date_from,
                date_to,
            )
        except MipymeAdapterError as error:
            return RecoveryResult(
                ERROR_STATUS.get(error.code, "indeterminate"),
                identity,
                error_code=error.code,
                diagnostic=error.diagnostic,
            )
        except Exception:
            return RecoveryResult(
                "transport_error",
                identity,
                error_code="SII_TRANSPORT_ERROR",
                diagnostic="Error inesperado al consultar el portal SII.",
            )

        matches = []
        for chunk in chunks:
            try:
                candidates = extract_candidates(chunk.xml)
            except ValueError as error:
                return RecoveryResult(
                    "invalid_xml",
                    identity,
                    error_code="XML_INVALIDO",
                    diagnostic=str(error),
                    chunks=len(chunks),
                )
            matches.extend(
                candidate for candidate in candidates if candidate.identity == identity
            )
        if not matches:
            return RecoveryResult(
                "not_found",
                identity,
                error_code="RESPALDO_SIN_DATOS",
                diagnostic="No se encontró un DTE con RUT, tipo y folio exactos.",
                chunks=len(chunks),
            )
        if len(matches) > 1:
            return RecoveryResult(
                "multiple_matches",
                identity,
                error_code="DTE_AMBIGUO",
                diagnostic=(
                    "El respaldo contiene más de un DTE con la identidad" "solicitada."
                ),
                chunks=len(chunks),
            )
        try:
            candidate = validate_candidate(matches[0], identity)
        except ValueError as error:
            return RecoveryResult(
                "invalid_xml",
                identity,
                error_code="XML_INVALIDO",
                diagnostic=str(error),
                chunks=len(chunks),
            )
        return RecoveryResult(
            "recovered",
            identity,
            candidate=candidate,
            digest=candidate.digest,
            chunks=len(chunks),
        )

    @staticmethod
    def _validate_dates(date_from, date_to):
        if not isinstance(date_from, date) or not isinstance(date_to, date):
            raise ValueError("Las fechas de búsqueda son obligatorias.")
        if date_from > date_to:
            raise ValueError(
                "La fecha inicial no puede ser posterior a la fecha final."
            )
