# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Client for the SII REST API of electronic boletas (DTE 39 and 41).

Boletas are not sent through DTEUpload: the SII REST service issues a seed
and a token on api.sii.cl (apicert.sii.cl for certification), receives the
signed EnvioBOLETA on rahue.sii.cl (pangal.sii.cl) and answers in JSON.
"""

import json
import re
from threading import RLock
from time import monotonic

import requests

from odoo.exceptions import ValidationError
from odoo.tools.translate import LazyTranslate

from odoo.addons.l10n_cl_dte_signing.services.certificate_identity import normalize_rut
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService

from .certification_auth import SiiTransportError
from .production_response_parser import parse_xml

_lt = LazyTranslate(__name__, default_lang="en_US")
HOSTS = {
    "certification": {
        "api": "https://apicert.sii.cl/recursos/v1/",
        "send": "https://pangal.sii.cl/recursos/v1/boleta.electronica.envio",
    },
    "production": {
        "api": "https://api.sii.cl/recursos/v1/",
        "send": "https://rahue.sii.cl/recursos/v1/boleta.electronica.envio",
    },
}
# The SII boleta service only accepts the User-Agent of its documentation.
USER_AGENT = "Mozilla/4.0 ( compatible; PROG 1.0; Windows NT)"
MAX_RESPONSE_BYTES = 2_000_000
TOKEN_TTL_SECONDS = 50 * 60
ACCEPTED = frozenset({"DOK"})
OBSERVED = frozenset({"RPR", "RLV"})
REJECTED = frozenset({"RCH", "RFR", "RCT", "RSC"})
_TOKENS = {}
_TOKENS_LOCK = RLock()


def _text(root, name):
    values = root.xpath("//*[local-name()=$name]/text()", name=name)
    return values[0].strip() if values else ""


def parse_json(content):
    if not isinstance(content, bytes) or len(content) > MAX_RESPONSE_BYTES:
        raise ValidationError(_lt("The SII boleta answer is empty or too large."))
    try:
        data = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ValidationError(_lt("The SII boleta answer is not JSON.")) from error
    if not isinstance(data, dict):
        raise ValidationError(_lt("The SII boleta answer is malformed."))
    return data


def parse_status(content, track_id, document_code):
    """Map a boleta status answer to accepted, rejected or processing."""
    data = parse_json(content)
    if str(data.get("trackid") or track_id) != str(track_id):
        raise ValidationError(_lt("The SII answer belongs to another TrackID."))
    code = str(data.get("estado") or "")
    result = {
        "state": "processing",
        "track_id": str(track_id),
        "transport_code": code,
        "raw_response": content,
        "error_summary": "",
    }
    details = [
        row
        for row in data.get("detalle_rep_rech") or []
        if str(row.get("tipo")) == str(document_code)
    ]
    states = {str(row.get("estado")) for row in details}
    if states & REJECTED or code in REJECTED:
        result["state"] = "rejected"
    elif states & OBSERVED:
        result["state"] = "accepted"
        result["error_summary"] = "; ".join(
            str(row.get("descripcion") or row.get("estado")) for row in details
        )
    elif code == "EPR":
        for row in data.get("estadistica") or []:
            if str(row.get("tipo")) != str(document_code):
                continue
            if row.get("rechazados"):
                result["state"] = "rejected"
            elif row.get("informados") and row.get("informados") == (
                (row.get("aceptados") or 0) + (row.get("reparos") or 0)
            ):
                result["state"] = "accepted"
                if row.get("reparos"):
                    result["error_summary"] = "SII accepted with observations."
    return result


class SiiBoletaRestClient:
    """Authenticate, send EnvioBOLETA and query its status on the REST API."""

    def __init__(self, environment, session=None):
        if environment not in HOSTS:
            raise ValidationError(_lt("Boletas are only sent in SII modes."))
        self.environment = environment
        self.session = session or requests.Session()
        self.session.trust_env = False

    def _request(self, method, url, *, dispatched=False, **kwargs):
        headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
        try:
            with self.session.request(
                method,
                url,
                headers=headers,
                timeout=(10, 60),
                allow_redirects=False,
                verify=True,
                stream=True,
                **kwargs,
            ) as response:
                content = response.raw.read(MAX_RESPONSE_BYTES + 1, decode_content=True)
                status = response.status_code
        except requests.RequestException:
            raise SiiTransportError(
                _lt("The connection with the SII boleta service failed."),
                dispatched=dispatched,
            ) from None
        if len(content) > MAX_RESPONSE_BYTES:
            raise SiiTransportError(
                _lt("The SII boleta answer exceeds the allowed size."),
                dispatched=dispatched,
            )
        return status, content

    def _cache_key(self, company):
        return (company.env.cr.dbname, company.id, self.environment)

    def invalidate_token(self, company):
        with _TOKENS_LOCK:
            _TOKENS.pop(self._cache_key(company), None)

    def token(self, company):
        key = self._cache_key(company)
        with _TOKENS_LOCK:
            cached = _TOKENS.get(key)
            if cached and cached[0] > monotonic():
                return cached[1]
        api = HOSTS[self.environment]["api"]
        status, content = self._request("GET", api + "boleta.electronica.semilla")
        seed = _text(parse_xml(content), "SEMILLA") if status == 200 else ""
        if not seed.isdigit():
            raise SiiTransportError(_lt("SII did not issue a boleta seed."))
        signed = SignatureService().sign_authentication_seed(
            company, self.environment, seed
        )
        status, content = self._request(
            "POST",
            api + "boleta.electronica.token",
            data=signed,
            headers={"Content-Type": "application/xml", "Accept": "application/xml"},
        )
        token = _text(parse_xml(content), "TOKEN") if status == 200 else ""
        if not token or not token.isalnum() or len(token) > 64:
            raise SiiTransportError(_lt("SII did not issue a boleta token."))
        with _TOKENS_LOCK:
            _TOKENS[key] = (monotonic() + TOKEN_TTL_SECONDS, token)
        return token

    def send_envelope(self, company, envelope, sender_vat):
        """Upload one signed EnvioBOLETA; retry once when the token expired."""
        sender_number, sender_dv = normalize_rut(sender_vat).split("-")
        issuer_number, issuer_dv = normalize_rut(company.vat).split("-")
        for retry in (False, True):
            status, content = self._request(
                "POST",
                HOSTS[self.environment]["send"],
                dispatched=True,
                headers={
                    "Accept": "application/json",
                    "Cookie": "TOKEN=" + self.token(company),
                },
                data={
                    "rutSender": sender_number,
                    "dvSender": sender_dv,
                    "rutCompany": issuer_number,
                    "dvCompany": issuer_dv,
                },
                files={"archivo": ("EnvioBOLETA.xml", envelope, "text/xml")},
            )
            if status != 401 or retry:
                break
            # An expired token is refused before SII reads the file.
            self.invalidate_token(company)
        if status != 200:
            raise SiiTransportError(
                _lt(
                    "SII answered HTTP %(status)s to the boleta upload.", status=status
                ),
                dispatched=status not in {400, 401, 403},
                response=content,
            )
        data = parse_json(content)
        track_id = str(data.get("trackid") or "")
        if not re.fullmatch(r"\d{1,15}", track_id):
            raise SiiTransportError(
                _lt("SII returned no TrackID for the boleta upload."),
                dispatched=True,
                response=content,
            )
        return {
            "state": "submitted",
            "track_id": track_id,
            "transport_code": str(data.get("estado") or ""),
            "raw_response": content,
        }

    def query(self, company, track_id, document_code):
        """Ask SII for the result of a boleta upload."""
        number, verifier = normalize_rut(company.vat).split("-")
        url = (
            HOSTS[self.environment]["api"]
            + f"boleta.electronica.envio/{number}-{verifier}-{int(track_id)}"
        )
        for retry in (False, True):
            status, content = self._request(
                "GET",
                url,
                headers={
                    "Accept": "application/json",
                    "Cookie": "TOKEN=" + self.token(company),
                },
            )
            if status != 401 or retry:
                break
            self.invalidate_token(company)
        if status != 200:
            raise SiiTransportError(
                _lt(
                    "SII answered HTTP %(status)s to the boleta status.", status=status
                ),
                response=content,
            )
        return parse_status(content, track_id, document_code)
