# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Parse the SII Registro de Transferencia de Créditos (RTC) responses.

Formats follow the SII developer manuals "Envío Automático de Archivos
Electrónicos de Cesión (AEC)" (RECEPCIONAEC receipt) and "WS Consulta de
Estado - RPETC" (wsRPETCConsulta getEstEnvio and getEstCesion).
"""

import re

from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tools.translate import LazyTranslate

from odoo.addons.l10n_cl_dte_gateway.services.certification_response_parser import (
    parse_xml,
    soap_payload,
    text,
)
from odoo.addons.l10n_cl_dte_signing.services.certificate_identity import normalize_rut

_lt = LazyTranslate(__name__, default_lang="en_US")

UPLOAD_TOKEN_REJECTED = "5"
# Codes that prove SII did not take the file: the operator may send again.
UPLOAD_RETRYABLE_CODES = frozenset({UPLOAD_TOKEN_REJECTED, "9", "10"})
UPLOAD_MESSAGES = {
    "0": _lt("Upload received by SII."),
    "1": _lt("The authenticated user cannot send on behalf of the assignor."),
    "2": _lt("SII rejected the size of the uploaded file."),
    "4": _lt("SII reported missing upload parameters."),
    "5": _lt("SII rejected the authentication token."),
    "6": _lt("The assignor is not an authorized DTE issuer."),
    "9": _lt("SII reported an internal error; the file was not received."),
    "10": _lt("SII reported an internal error; the file was not received."),
}
# getEstEnvio ESTADO_ENVIO values grouped by assignment outcome.
UPLOAD_PROCESSING = frozenset({"UPL", "RCP", "SOK", "FSO", "COK", "VDC", "VCS"})
UPLOAD_REJECTED = frozenset({"RSC", "RFS", "RCR", "RDC", "RCS"})
UPLOAD_ACCEPTED = frozenset({"EOK"})
UPLOAD_ANNULLED = frozenset({"EAN"})
# wsRPETCConsulta RESP_HDR/ESTADO codes caused by an invalid or expired token.
QUERY_TOKEN_REJECTED = frozenset({"3", "4", "5"})


def upload_message(code):
    """Describe one RECEPCIONAEC STATUS code."""
    message = UPLOAD_MESSAGES.get(code)
    if message:
        return str(message)
    return str(_lt("SII returned upload status %(code)s.", code=code))


def parse_upload_receipt(content, assignor_vat):
    """Return the RTC upload receipt bound to the assignor company."""
    root = parse_xml(content)
    if etree.QName(root).localname != "RECEPCIONAEC":
        raise ValidationError(_lt("SII did not return an AEC upload receipt."))
    if normalize_rut(text(root, "RUTCOMPANY")) != normalize_rut(assignor_vat):
        raise ValidationError(
            _lt("The SII upload receipt belongs to a different assignor.")
        )
    code = text(root, "STATUS")
    track_id = text(root, "TRACKID", required=False)
    if code == "0":
        if not re.fullmatch(r"0*[1-9]\d{0,14}", track_id):
            raise ValidationError(_lt("The SII upload receipt has no valid TrackID."))
        state = "submitted"
    elif code in UPLOAD_RETRYABLE_CODES:
        state = "not_received"
    else:
        state = "rejected"
    return {
        "state": state,
        "code": code,
        "track_id": track_id.lstrip("0") if code == "0" else "",
        "message": upload_message(code),
        "raw_response": content,
    }


def _optional_text(root, name):
    values = root.xpath(".//*[local-name()=$name]/text()", name=name)
    return values[0].strip() if len(values) == 1 else ""


def _payload_header(payload):
    return text(payload, "ESTADO"), _optional_text(payload, "GLOSA")


def parse_upload_status(content, track_id):
    """Map getEstEnvio to processing, accepted, rejected or annulled."""
    payload = soap_payload(content, "getEstEnvio")
    code, glosa = _payload_header(payload)
    if code != "0":
        return {"state": "query_error", "code": code, "message": glosa}
    returned = text(payload, "TRACKID")
    if returned.lstrip("0") != str(track_id).lstrip("0"):
        raise ValidationError(_lt("SII answered for a different TrackID."))
    status = text(payload, "ESTADO_ENVIO")
    description = _optional_text(payload, "DESC_ESTADO")
    if status in UPLOAD_ACCEPTED:
        state = "accepted"
    elif status in UPLOAD_REJECTED:
        state = "rejected"
    elif status in UPLOAD_ANNULLED:
        state = "annulled"
    elif status in UPLOAD_PROCESSING:
        state = "processing"
    else:
        raise ValidationError(
            _lt("SII returned an unknown AEC status %(status)s.", status=status)
        )
    return {
        "state": state,
        "code": status,
        "message": f"{status}: {description}" if description else status,
    }


def parse_assignment_status(content):
    """Read getEstCesion without IdCesion: ESTADO 0 means the DTE is assigned."""
    payload = soap_payload(content, "getEstCesion")
    code, glosa = _payload_header(payload)
    return {
        "assigned": code == "0",
        "code": code,
        "message": glosa or code,
    }
