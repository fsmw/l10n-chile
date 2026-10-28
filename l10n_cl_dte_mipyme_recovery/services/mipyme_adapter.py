# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Authenticated adapter for the legacy SII MiPyme emitted-document portal."""

import base64
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from tempfile import NamedTemporaryFile
from urllib.parse import urlencode

import requests

RESPALDO_BASE = "https://www1.sii.cl/cgi-bin/Portal001"
RESPALDO_MAX_DOCS = 20
TARGET = "https://misiir.sii.cl/cgi_misii/siihome.cgi"
RESPALDO_LIST_URL = f"{RESPALDO_BASE}/lista_documentos.cgi"
RESPALDO_ORIGIN = "https://www1.sii.cl"


@dataclass(frozen=True)
class PortalChunk:
    date_from: date
    date_to: date
    total: int
    xml: bytes


class MipymeAdapterError(Exception):
    """A safe, stable error returned by the MiPyme adapter."""

    def __init__(self, code, message, diagnostic=""):
        super().__init__(message)
        self.code = code
        self.diagnostic = diagnostic or message


def classify_portal_response(body):
    """Classify the legacy portal response.

    Template alerts must not be mistaken for facts.
    """
    text = (
        body.decode("ISO-8859-1", errors="replace")
        if isinstance(body, bytes)
        else str(body or "")
    )
    alerts = []
    for match in re.finditer(r"alert\s*\(\s*(['\"])([\s\S]*?)\1\s*\)", text, re.I):
        line_start = text.rfind("\n", 0, match.start()) + 1
        before = text[line_start : match.start()]
        if "//" in re.sub(r"[a-z]+://", "", before, flags=re.I):
            continue
        alerts.append(re.sub(r"\s+", " ", match.group(2)).strip())
    if re.search(r"recaptcha", text, re.I) and not alerts:
        raise MipymeAdapterError(
            "RESPALDO_CAPTCHA", "El portal MiPyme requiere captcha."
        )
    if len(alerts) > 1:
        raise MipymeAdapterError(
            "RESPALDO_INDETERMINADO",
            "El portal MiPyme devolvió mensajes contradictorios.",
            " | ".join(alerts),
        )
    if alerts:
        message = alerts[0]
        code = (
            "RESPALDO_SIN_DATOS"
            if re.search(r"no existe informaci[oó]n", message, re.I)
            else "RESPALDO_RECHAZADO"
        )
        raise MipymeAdapterError(code, message, message)
    if re.search(r"Seleccionar empresa", text, re.I):
        raise MipymeAdapterError(
            "RESPALDO_SIN_EMPRESA",
            "El portal no identificó la empresa; la causa no está determinada.",
        )
    raise MipymeAdapterError(
        "RESPALDO_INDETERMINADO", "Respuesta no reconocida del portal MiPyme."
    )


class ProductionMipymeAdapter:
    """Download emitted DTE backups using the configured company certificate.

    The HTTP session is deliberately injectable for tests.  In production the
    request is made directly in Python through a temporary client-certificate
    transport; no Node subprocess or persisted password/session cookie is used.
    """

    def __init__(self, env, session_factory=None):
        self.env = env
        self.session_factory = session_factory or requests.Session

    def retrieve(self, company, issuer_rut, dte_type, date_from, date_to):
        if getattr(company, "l10n_cl_dte_service_provider", "SII") != "SII":
            raise MipymeAdapterError(
                "MIPYME_PRODUCTION_ONLY",
                "La recuperación MiPyme solo está disponible en producción.",
            )
        certificate = self._certificate(company)
        session = self.session_factory()
        with self._certificate_files(certificate) as certificate_paths:
            self._configure_certificate_transport(session)
            try:
                self._authenticate(session, certificate_paths)
                rut, verifier = issuer_rut.split("-", 1)
                chunks = []
                for start, end, total in self._split_ranges(
                    session, rut, verifier, dte_type, date_from, date_to
                ):
                    chunks.append(
                        PortalChunk(
                            start,
                            end,
                            total,
                            self._download(
                                session, rut, verifier, dte_type, start, end
                            ),
                        )
                    )
                return chunks
            except MipymeAdapterError:
                raise
            except requests.RequestException as error:
                raise self._transport_error("consultar el portal SII", error) from error

    def _get_signature(self, company):
        """Return a valid certificate for the company.

        ``certificate.certificate`` is a Community module: ``company_id`` and
        ``is_valid`` always exist. ``user_id`` belongs to the DTE localization,
        so it is used only when present.
        """
        Certificate = self.env["certificate.certificate"]
        domain = [("company_id", "=", company.id), ("is_valid", "=", True)]
        if "user_id" in Certificate._fields:
            signature = Certificate.search(
                domain + [("user_id", "=", self.env.uid)], limit=1
            )
            if signature:
                return signature
            return Certificate.search(domain + [("user_id", "=", False)], limit=1)
        return Certificate.search(domain, limit=1)

    def _certificate(self, company):
        try:
            certificate = self._get_signature(company)
        except Exception as error:
            raise MipymeAdapterError(
                "SII_AUTHENTICATION_FAILED",
                "No se encontró un certificado digital válido para la compañía.",
            ) from error
        if (
            not certificate
            or not certificate.pem_certificate
            or not certificate.private_key_id
        ):
            raise MipymeAdapterError(
                "SII_AUTHENTICATION_FAILED",
                "La compañía no tiene un certificado digital utilizable.",
            )
        return certificate

    @staticmethod
    @contextmanager
    def _certificate_files(certificate):
        """Expose certificate material as temporary files for requests."""
        try:
            certificate_pem = base64.b64decode(
                certificate.with_context(bin_size=False).pem_certificate
            )
            private_key_pem = base64.b64decode(
                certificate.private_key_id.with_context(bin_size=False).pem_key
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise MipymeAdapterError(
                "SII_AUTHENTICATION_FAILED",
                "El certificado digital no pudo ser decodificado.",
                f"certificate_decode:{type(error).__name__}",
            ) from error
        if not certificate_pem or not private_key_pem:
            raise MipymeAdapterError(
                "SII_AUTHENTICATION_FAILED",
                "El certificado digital no contiene material utilizable.",
            )
        with (
            NamedTemporaryFile(mode="wb") as certificate_file,
            NamedTemporaryFile(mode="wb") as private_key_file,
        ):
            certificate_file.write(certificate_pem)
            certificate_file.flush()
            private_key_file.write(private_key_pem)
            private_key_file.flush()
            yield certificate_file.name, private_key_file.name

    @staticmethod
    def _configure_certificate_transport(session):
        session.headers.update(
            {
                "Origin": RESPALDO_ORIGIN,
                "Referer": RESPALDO_LIST_URL,
            }
        )
        session.cookies.set("usa_firma_central", "true")

    @staticmethod
    def _transport_error(stage, error):
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        return MipymeAdapterError(
            "SII_TRANSPORT_ERROR",
            f"Falló el transporte al {stage}.",
            f"{stage}:{type(error).__name__}" + (f":HTTP_{status}" if status else ""),
        )

    @staticmethod
    def _authenticate(session, certificate_paths):
        try:
            session.get(
                f"https://zeusr.sii.cl/AUT2000/InicioAutenticacion/IngresoCertificado.html?{TARGET}",
                timeout=30,
            )
            response = session.post(
                f"https://herculesr.sii.cl/cgi_AUT2000/CAutInicio.cgi?{TARGET}",
                data={"referencia": TARGET},
                cert=certificate_paths,
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            raise ProductionMipymeAdapter._transport_error(
                "autenticar contra SII", error
            ) from error
        if (
            "mximo de sesiones" in response.text.lower()
            or "máximo de sesiones" in response.text.lower()
        ):
            raise MipymeAdapterError(
                "SII_AUTHENTICATION_FAILED",
                "SII rechazó la sesión por límite de sesiones.",
            )
        if not any(
            key.startswith("NETSCAPE_LIVEWIRE") for key in session.cookies.keys()
        ):
            raise MipymeAdapterError(
                "SII_AUTHENTICATION_FAILED", "SII no entregó una sesión autenticada."
            )

    @staticmethod
    def _split_ranges(session, rut, verifier, dte_type, date_from, date_to):
        ranges = []

        def split(start, end):
            total = ProductionMipymeAdapter._count(
                session, rut, verifier, dte_type, start, end
            )
            if total == 0:
                return
            if total <= RESPALDO_MAX_DOCS:
                ranges.append((start, end, total))
                return
            if start == end:
                raise MipymeAdapterError(
                    "RESPALDO_RECHAZADO",
                    f"El {start} supera el límite de {RESPALDO_MAX_DOCS} "
                    "documentos y no se puede dividir.",
                )
            midpoint = start + (end - start) // 2
            split(start, midpoint)
            split(midpoint + timedelta(days=1), end)

        split(date_from, date_to)
        ranges.sort(key=lambda item: item[0], reverse=True)
        return ranges

    @staticmethod
    def _count(session, rut, verifier, dte_type, date_from, date_to):
        body = ProductionMipymeAdapter._request_list(
            session, rut, verifier, dte_type, date_from, date_to
        )
        text = body.decode("ISO-8859-1", errors="replace")
        match = re.search(r"total de documentos[^0-9]{0,40}(\d+)", text, re.I)
        if not match:
            classify_portal_response(body)
        return int(match.group(1))

    @staticmethod
    def _request_list(session, rut, verifier, dte_type, date_from, date_to):
        data = {
            "recaptcha-response": "",
            "ORIGEN": "ENV",
            "FEC_DESDE": date_from.isoformat(),
            "FEC_HASTA": date_to.isoformat(),
            "NUM_PAG": "1",
            "TPO_ARCHIVO": "dte",
            "RUT_EMP": rut,
            "DV_EMP": verifier,
            "RUT_RECP": "",
            "FOLIO": "",
            "FOLIOHASTA": "",
            "RZN_SOC": "",
            "TPO_DOC": dte_type,
            "ESTADO": "",
            "ORDEN": "",
        }
        try:
            response = session.post(
                RESPALDO_LIST_URL,
                data=urlencode(data),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": RESPALDO_ORIGIN,
                    "Referer": RESPALDO_LIST_URL,
                },
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            raise ProductionMipymeAdapter._transport_error(
                "consultar documentos SII", error
            ) from error
        return response.content

    @staticmethod
    def _download(session, rut, verifier, dte_type, date_from, date_to):
        params = {
            "RUT_EMP": rut,
            "DV_EMP": verifier,
            "RUT_RECP": "",
            "FOLIO": "",
            "FOLIOHASTA": "",
            "RZN_SOC": "",
            "TPO_DOC": dte_type,
            "ESTADO": "",
            "ORDEN": "",
            "ORIGEN": "ENV",
            "FEC_DESDE": date_from.isoformat(),
            "FEC_HASTA": date_to.isoformat(),
            "DOWNLOAD": "XML",
        }
        try:
            response = session.get(
                f"{RESPALDO_BASE}/download.cgi?{urlencode(params)}",
                headers={
                    "Origin": RESPALDO_ORIGIN,
                    "Referer": RESPALDO_LIST_URL,
                },
                timeout=60,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            raise ProductionMipymeAdapter._transport_error(
                "descargar XML SII", error
            ) from error
        body = response.content
        if not body.lstrip().startswith(b"<?xml"):
            classify_portal_response(body)
        return body
