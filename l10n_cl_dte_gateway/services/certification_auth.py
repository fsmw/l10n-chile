# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from threading import RLock
from time import monotonic

import requests
from lxml import etree

from odoo import _
from odoo.exceptions import UserError

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService

from .certification_response_parser import soap_payload, text

BASE_URL = "https://maullin.sii.cl"
PRODUCTION_BASE_URL = "https://palena.sii.cl"
_ALLOWED_PATHS = {
    "/DTEWS/CrSeed.jws",
    "/DTEWS/GetTokenFromSeed.jws",
    "/DTEWS/QueryEstUp.jws",
    "/DTEWS/QueryEstDte.jws",
    "/cgi_dte/UPL/DTEUpload",
}
_TOKEN_TTL_SECONDS = 50 * 60
_TOKEN_CACHE = {}
_TOKEN_CACHE_LOCK = RLock()
SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
XSI = "http://www.w3.org/2001/XMLSchema-instance"


class SiiTransportError(UserError):
    """A safe diagnostic; never include HTTP exceptions containing credentials."""

    def __init__(self, message, *, dispatched=False, response=None, code=None):
        super().__init__(message)
        self.dispatched = dispatched
        self.outcome_unknown = dispatched
        self.response = response
        self.code = code


class CertificationAuthentication:
    """Use SII's certificate-signed seed/token contract on the fixed test host."""

    environment = "certification"
    base_url = BASE_URL

    def __init__(self, session=None):
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.update(
            {"User-Agent": "Mozilla/4.0 (compatible; PROG 1.0)"}
        )

    def post(self, path, *, upload=False, **kwargs):
        if path not in _ALLOWED_PATHS:
            raise SiiTransportError(
                _("The SII operation is not available for this transport.")
            )
        try:
            with self.session.post(
                self.base_url + path,
                timeout=(10, 60),
                allow_redirects=False,
                verify=True,
                stream=True,
                **kwargs,
            ) as response:
                if response.status_code != 200:
                    raise SiiTransportError(
                        _(
                            "SII returned HTTP %(status)s. "
                            "The operation was not confirmed."
                        )
                        % {"status": response.status_code},
                        dispatched=upload,
                    )
                chunks, length = [], 0
                for chunk in response.iter_content(65536):
                    length += len(chunk)
                    if length > 2_000_000:
                        raise SiiTransportError(
                            _("The SII response exceeds the allowed size."),
                            dispatched=upload,
                        )
                    chunks.append(chunk)
                return b"".join(chunks)
        except requests.RequestException:
            raise SiiTransportError(
                _("The connection with SII could not be completed."), dispatched=upload
            ) from None

    def soap(self, service, operation, parameters):
        root = etree.Element(
            f"{{{SOAP}}}Envelope",
            nsmap={
                "soapenv": SOAP,
                "xsi": XSI,
                "xsd": "http://www.w3.org/2001/XMLSchema",
            },
        )
        body = etree.SubElement(root, f"{{{SOAP}}}Body")
        # The current Certification WSDL bindings use this RPC namespace.
        # The historical PDF examples instead show the endpoint URL.
        namespace = (
            "http://DefaultNamespace"
            if self.environment == "certification"
            else f"{self.base_url}/DTEWS/{service}.jws"
        )
        method = etree.SubElement(body, f"{{{namespace}}}{operation}")
        method.set(
            f"{{{SOAP}}}encodingStyle", "http://schemas.xmlsoap.org/soap/encoding/"
        )
        for name, value in parameters.items():
            etree.SubElement(method, name, {f"{{{XSI}}}type": "xsd:string"}).text = (
                etree.CDATA(value) if name == "pszXml" else value
            )
        return self.post(
            "/DTEWS/" + service + ".jws",
            data=etree.tostring(root, encoding="UTF-8", xml_declaration=True),
            headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'},
        )

    def token(self, company):
        self.env = company.env
        signature_service = SignatureService()
        if self.environment == "certification":
            certificate, _sender = signature_service.certification_signing_identity(
                company
            )
            profile = (
                company.env["l10n_cl.dte.signing.profile"]
                .sudo()
                .search(
                    [
                        ("company_id", "=", company.id),
                        ("sii_environment", "=", self.environment),
                        ("active", "=", True),
                    ],
                    limit=1,
                )
            )
        else:
            profile, _sender = signature_service.signing_identity(
                company, self.environment
            )
            certificate = profile.sudo().certificate_id
        key = (
            company.env.cr.dbname,
            company.id,
            self.environment,
            profile.id,
            profile.write_date,
            certificate.id,
            certificate.write_date,
            certificate.private_key_id.id,
            certificate.private_key_id.write_date,
        )
        with _TOKEN_CACHE_LOCK:
            cached = _TOKEN_CACHE.get(key)
            if cached and cached[0] > monotonic():
                return cached[1]
        seed_response = soap_payload(self.soap("CrSeed", "getSeed", {}), "getSeed")
        if text(seed_response, "ESTADO") != "00":
            raise SiiTransportError(_("SII did not authorize an authentication seed."))
        signed = SignatureService().sign_authentication_seed(
            company, self.environment, text(seed_response, "SEMILLA")
        )
        token_response = soap_payload(
            self.soap(
                "GetTokenFromSeed", "getToken", {"pszXml": signed.decode("UTF-8")}
            ),
            "getToken",
        )
        if text(token_response, "ESTADO") != "00":
            raise SiiTransportError(
                _("SII rejected certificate authentication (code %(code)s).")
                % {"code": text(token_response, "ESTADO")},
                code=text(token_response, "ESTADO"),
            )
        token = text(token_response, "TOKEN")
        if not token.isalnum() or len(token) > 64:
            raise SiiTransportError(_("SII returned an invalid authentication token."))
        with _TOKEN_CACHE_LOCK:
            _TOKEN_CACHE[key] = (monotonic() + _TOKEN_TTL_SECONDS, token)
        return token

    def close(self):
        self.session.cookies.clear()
        self.session.close()


class ProductionSiiAuthentication(CertificationAuthentication):
    """Use the same authenticated SII service contract on the fixed live host."""

    environment = "production"
    base_url = PRODUCTION_BASE_URL
