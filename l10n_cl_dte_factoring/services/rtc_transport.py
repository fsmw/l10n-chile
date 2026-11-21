# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Upload AECs to the SII RTC and query their status.

Authentication reuses the gateway's certificate-signed seed/token clients and
their fixed hosts (maullin for Certification, palena for Production). This
module only adds the two RTC paths, with the same bounded timeouts, size limit,
generic User-Agent and no redirects. The token is sent as a cookie and never
logged or stored.
"""

import requests
from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tools.translate import LazyTranslate

from odoo.addons.l10n_cl_dte_gateway.services.certification_auth import (
    SOAP,
    XSI,
    SiiTransportError,
)
from odoo.addons.l10n_cl_dte_gateway.services.sii_authentication import (
    CertificationAuthentication,
    ProductionSiiAuthentication,
)
from odoo.addons.l10n_cl_dte_signing.services.certificate_identity import normalize_rut

from .rtc_response_parser import (
    QUERY_TOKEN_REJECTED,
    UPLOAD_TOKEN_REJECTED,
    parse_assignment_status,
    parse_upload_receipt,
    parse_upload_status,
)

_lt = LazyTranslate(__name__, default_lang="en_US")

UPLOAD_PATH = "/cgi_rtc/RTC/RTCAnotEnvio.cgi"
QUERY_PATH = "/DTEWS/services/wsRPETCConsulta"
QUERY_NAMESPACE = "http://DefaultNamespace"
TIMEOUT = (10, 60)
MAX_RESPONSE_BYTES = 2_000_000
AUTHENTICATIONS = {
    "certification": CertificationAuthentication,
    "production": ProductionSiiAuthentication,
}


def split_rut(value):
    """Return the body and check digit of a Chilean RUT."""
    number, verifier = normalize_rut(value).split("-")
    return number, verifier


class RtcTransport:
    """One RTC client bound to a real SII environment."""

    def __init__(self, environment, authentication_factory=None):
        if environment not in AUTHENTICATIONS:
            raise ValidationError(
                _lt("AEC transmission needs the Certification or Production mode.")
            )
        self.environment = environment
        self.authentication_factory = (
            authentication_factory or AUTHENTICATIONS[environment]
        )

    def upload(self, company, aec_bytes, assignor_vat, notify_email):
        """Upload one signed AEC; return the bound RECEPCIONAEC receipt."""
        number, verifier = split_rut(assignor_vat)
        auth = self.authentication_factory()
        try:
            for retry in (False, True):
                token = auth.token(company)
                response = self._post(
                    auth,
                    UPLOAD_PATH,
                    upload=True,
                    data={
                        "emailNotif": notify_email,
                        "rutCompany": number,
                        "dvCompany": verifier,
                    },
                    files={"archivo": ("AEC.xml", aec_bytes, "text/xml")},
                    headers={
                        "Cookie": "TOKEN=" + token,
                        "Referer": auth.base_url,
                        "Accept": "application/xml",
                    },
                )
                try:
                    result = parse_upload_receipt(response, assignor_vat)
                except ValidationError as error:
                    raise SiiTransportError(
                        str(error), dispatched=True, response=response
                    ) from None
                if retry or result["code"] != UPLOAD_TOKEN_REJECTED:
                    return result
                # SII refused the cached token before reading the file.
                auth.invalidate_token(company)
            return result
        finally:
            auth.close()

    def query_upload(self, company, track_id):
        """Return the processing state of one RTC upload TrackID."""
        if not str(track_id or "").isdigit():
            raise ValidationError(_lt("A numeric SII TrackID is required."))
        return self._query(
            company,
            "getEstEnvio",
            {"TrackId": str(track_id)},
            lambda response: parse_upload_status(response, track_id),
        )

    def query_assignment(self, company, issuer_vat, document_code, folio):
        """Ask whether SII records the DTE as assigned (getEstCesion)."""
        number, verifier = split_rut(issuer_vat)
        parameters = {
            "RutEmisor": number,
            "DVEmisor": verifier,
            "TipoDoc": str(document_code),
            "FolioDoc": str(folio),
            "IdCesion": "",
        }
        return self._query(company, "getEstCesion", parameters, parse_assignment_status)

    def _query(self, company, operation, parameters, parser):
        auth = self.authentication_factory()
        try:
            for retry in (False, True):
                response = self._soap(
                    auth, operation, {"Token": auth.token(company), **parameters}
                )
                try:
                    result = parser(response)
                except ValidationError as error:
                    raise SiiTransportError(str(error), response=response) from None
                result["raw_response"] = response
                if retry or result.get("code") not in QUERY_TOKEN_REJECTED:
                    return result
                auth.invalidate_token(company)
            return result
        finally:
            auth.close()

    def _soap(self, auth, operation, parameters):
        root = etree.Element(
            f"{{{SOAP}}}Envelope",
            nsmap={
                "soapenv": SOAP,
                "xsi": XSI,
                "xsd": "http://www.w3.org/2001/XMLSchema",
            },
        )
        body = etree.SubElement(root, f"{{{SOAP}}}Body")
        method = etree.SubElement(body, f"{{{QUERY_NAMESPACE}}}{operation}")
        method.set(
            f"{{{SOAP}}}encodingStyle", "http://schemas.xmlsoap.org/soap/encoding/"
        )
        for name, value in parameters.items():
            node = etree.SubElement(method, name, {f"{{{XSI}}}type": "xsd:string"})
            node.text = value
        return self._post(
            auth,
            QUERY_PATH,
            data=etree.tostring(root, encoding="UTF-8", xml_declaration=True),
            headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'},
        )

    @staticmethod
    def _post(auth, path, *, upload=False, **kwargs):
        """POST to the fixed SII host; an upload failure is an unknown outcome."""
        if path not in {UPLOAD_PATH, QUERY_PATH}:
            raise SiiTransportError(_lt("The RTC operation is not available."))
        try:
            with auth.session.post(
                auth.base_url + path,
                timeout=TIMEOUT,
                allow_redirects=False,
                verify=True,
                stream=True,
                **kwargs,
            ) as response:
                if response.status_code != 200:
                    raise SiiTransportError(
                        _lt(
                            "SII returned HTTP %(status)s. The operation was not "
                            "confirmed.",
                            status=response.status_code,
                        ),
                        dispatched=upload,
                    )
                chunks, length = [], 0
                for chunk in response.iter_content(65536):
                    length += len(chunk)
                    if length > MAX_RESPONSE_BYTES:
                        raise SiiTransportError(
                            _lt("The SII response exceeds the allowed size."),
                            dispatched=upload,
                        )
                    chunks.append(chunk)
                return b"".join(chunks)
        except requests.RequestException:
            # Never chain the exception: it can carry the token cookie.
            raise SiiTransportError(
                _lt("The connection with SII could not be completed."),
                dispatched=upload,
            ) from None
