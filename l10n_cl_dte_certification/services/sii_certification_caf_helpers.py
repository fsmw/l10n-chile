# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Parsing and validation helpers for the SII Certification CAF assistant."""

import re
import unicodedata
from datetime import date
from urllib.parse import urljoin, urlsplit

from lxml import etree
from lxml import html as lxml_html

from odoo.addons.l10n_cl_dte_core.tools.xml_safety import safe_fromstring

from .sii_certification_caf_types import (
    CafAllocationRejected,
    CafRequestRejected,
    CafResult,
    CafSessionExpired,
    _FormParser,
)

MAX_RESPONSE_BYTES = 1024 * 1024
LOGIN_MARKERS = (
    "autInicioDTE",
    "IngresoCertificado",
    "InicioAutenticacion",
    "CAutInicio.cgi",
    "CAutValida",
)
ERROR_MARKERS = (
    "El código de este mensaje es",
    "no se encuentra autorizado",
    "no está autorizado",
    "Usted no tiene",
    "no es representante",
    "no puede solicitar",
)


def _validate_sii_host(url, allow_subdomains=False):
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if (
        parts.scheme != "https"
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
        or not (host == "sii.cl" or (allow_subdomains and host.endswith(".sii.cl")))
    ):
        raise CafRequestRejected("redirect_outside_sii")


def _validate_assistant_url(url):
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or (parts.hostname or "").lower() != "maullin.sii.cl"
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
    ):
        raise CafRequestRejected("assistant_url_outside_certification")


def _validate_recovery_url(url):
    _validate_assistant_url(url)
    if urlsplit(url).path not in {
        "/cvc_cgi/dte/rf_reobtencion1_folios",
        "/cvc_cgi/dte/rf_reobtencion2_folios",
        "/cvc_cgi/dte/rf_reobtencion3_folios",
        "/cvc_cgi/dte/rf_genera_folio",
        "/cvc_cgi/dte/rf_genera_archivo",
    }:
        raise CafRequestRejected("caf_recovery_url_forbidden")


def _check_recovery_session(page):
    _check_session(page)
    if "no se encuentra autenticado" in page.lower():
        raise CafSessionExpired("sii_session_expired")


def _host(url):
    return (urlsplit(url).hostname or "").lower()


def _resolve_url(base, reference):
    return urljoin(base, (reference or "").strip())


def _parse_form(html):
    parser = _FormParser()
    try:
        parser.feed(html)
        parser.close()
    except (AssertionError, ValueError) as error:
        raise CafRequestRejected("sii_form_invalid") from error
    if parser.action is None:
        _check_sii_error(html)
        raise CafRequestRejected("sii_form_missing")
    return {"action": parser.action, "inputs": parser.inputs}


def _decode_html(content):
    if content.startswith(b"\xef\xbb\xbf"):
        content = content[3:]
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("iso-8859-1")


def _check_session(text):
    if any(marker in text for marker in LOGIN_MARKERS):
        raise CafSessionExpired("sii_session_expired")


def _check_sii_error(html):
    # Only an explicit non-completion response is a definite allocation failure.
    text = " ".join(lxml_html.fromstring(html).text_content().split()).lower()
    normalized = "".join(
        char
        for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )
    if (
        "no ha sido posible completar su solicitud" in normalized
        and "cantidad de documentos a timbrar debe ser menor o igual" in normalized
        and "maximo autorizado" in normalized
    ):
        raise CafAllocationRejected("sii_quantity_limit_exceeded")
    lower = html.lower()
    if any(marker.lower() in lower for marker in ERROR_MARKERS):
        raise CafRequestRejected("sii_rejected_request")


def _check_quantity_limit(inputs, quantity):
    maximum = (inputs.get("MAX_AUTOR") or "").strip()
    if maximum.isdigit() and quantity > int(maximum):
        raise CafAllocationRejected("sii_quantity_limit_exceeded", int(maximum))


def _split_vat(value):
    normalized = re.sub(r"[^0-9Kk-]", "", value or "").upper()
    if "-" not in normalized:
        raise CafRequestRejected("company_vat_invalid")
    rut, dv = normalized.rsplit("-", 1)
    if not rut.isdigit() or len(dv) != 1 or not (dv.isdigit() or dv == "K"):
        raise CafRequestRejected("company_vat_invalid")
    return rut, dv


def _normalize_vat(value):
    return re.sub(r"[^0-9Kk]", "", value or "").upper()


def _parse_caf(content):
    if not content or len(content) > MAX_RESPONSE_BYTES:
        raise CafRequestRejected("caf_size_invalid")
    upper = content.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise CafRequestRejected("caf_dtd_forbidden")
    try:
        root = safe_fromstring(content)
    except (etree.XMLSyntaxError, ValueError) as error:
        raise CafRequestRejected("caf_xml_invalid") from error
    if etree.QName(root).localname != "AUTORIZACION":
        raise CafRequestRejected("caf_root_invalid")
    data_nodes = root.xpath("./*[local-name()='CAF']/*[local-name()='DA']")
    if len(data_nodes) != 1:
        raise CafRequestRejected("caf_metadata_missing")
    data = data_nodes[0]

    def text(path):
        nodes = data.xpath(path)
        return (nodes[0].text or "").strip() if len(nodes) == 1 else ""

    try:
        folio_start = int(text("./*[local-name()='RNG']/*[local-name()='D']"))
        folio_end = int(text("./*[local-name()='RNG']/*[local-name()='H']"))
        date_from = date.fromisoformat(text("./*[local-name()='FA']"))
    except ValueError as error:
        raise CafRequestRejected("caf_metadata_invalid") from error
    values = {
        "document_code": text("./*[local-name()='TD']"),
        "issuer_vat": text("./*[local-name()='RE']"),
        "idk": text("./*[local-name()='IDK']"),
    }
    if not all(values.values()) or folio_start < 1 or folio_end < folio_start:
        raise CafRequestRejected("caf_metadata_invalid")
    return CafResult(
        content=content,
        folio_start=folio_start,
        folio_end=folio_end,
        date_from=date_from,
        **values,
    )
