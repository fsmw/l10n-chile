# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Client for the SII registry of acceptances and claims on received DTEs.

``ingresarAceptacionReclamoDoc`` registers ACD, RCD, ERM, RFP or RFT for a
supplier DTE (Law 20.956); ``listarEventosHistDoc`` lists the events on a DTE.
Both are SOAP 1.1 RPC operations authenticated with the SII token cookie.
"""

import requests
from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tools.translate import LazyTranslate

from odoo.addons.l10n_cl_dte_signing.services.certificate_identity import normalize_rut

from .certification_auth import SiiTransportError
from .production_response_parser import parse_xml
from .sii_authentication import CertificationAuthentication, ProductionSiiAuthentication

_lt = LazyTranslate(__name__, default_lang="en_US")
SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
SERVICE_NAMESPACE = "http://ws.registroreclamodte.diii.sdi.sii.cl"
ENDPOINTS = {
    "certification": "https://ws2.sii.cl/WSREGISTRORECLAMODTECERT/"
    "registroreclamodteservice",
    "production": "https://ws1.sii.cl/WSREGISTRORECLAMODTE/registroreclamodteservice",
}
ACTIONS = frozenset({"ACD", "RCD", "ERM", "RFP", "RFT"})
# codResp 0 registers the action; SII also answers 1 when it accepted it.
SUCCESS_CODES = frozenset({0, 1})
MAX_RESPONSE_BYTES = 1_000_000


def _child_text(node, name):
    values = node.xpath("./*[local-name()=$name]/text()", name=name)
    return values[0].strip() if values else ""


def parse_claim_response(content, operation):
    """Return the respuestaTo of one registry operation as a plain mapping."""
    root = parse_xml(content)
    faults = root.xpath("//*[local-name()='Fault']")
    if faults:
        raise ValidationError(
            _lt(
                "The SII claim registry returned a SOAP fault: %(fault)s",
                fault=_child_text(faults[0], "faultstring") or "?",
            )
        )
    returns = root.xpath(
        "//*[local-name()=$op]/*[local-name()='return']", op=operation + "Response"
    )
    if len(returns) != 1:
        raise ValidationError(_lt("The SII claim registry answer is malformed."))
    node = returns[0]
    code = _child_text(node, "codResp")
    if not code.lstrip("-").isdigit():
        raise ValidationError(_lt("The SII claim registry returned no response code."))
    events = [
        {
            "code": _child_text(event, "codEvento"),
            "description": _child_text(event, "descEvento"),
            "responsible": "-".join(
                part
                for part in (
                    _child_text(event, "rutResponsable"),
                    _child_text(event, "dvResponsable"),
                )
                if part
            ),
            "date": _child_text(event, "fechaEvento"),
        }
        for event in node.xpath("./*[local-name()='listaEventosDoc']")
    ]
    return {
        "code": int(code),
        "description": _child_text(node, "descResp"),
        "events": events,
        "success": int(code) in SUCCESS_CODES,
        "raw_response": content,
    }


class SiiClaimRegistryClient:
    """Register and list acceptance and claim events on a received DTE."""

    authentication = {
        "certification": CertificationAuthentication,
        "production": ProductionSiiAuthentication,
    }

    def __init__(self, environment, session=None, authentication_factory=None):
        if environment not in ENDPOINTS:
            raise ValidationError(
                _lt("The SII claim registry is only available in SII modes.")
            )
        self.environment = environment
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.authentication_factory = (
            authentication_factory or self.authentication[environment]
        )

    def register_action(self, company, issuer_vat, document_code, folio, action):
        """Register ACD, RCD, ERM, RFP or RFT for a supplier DTE."""
        if action not in ACTIONS:
            raise ValidationError(_lt("Unknown acceptance or claim action."))
        return self._call(
            company,
            "ingresarAceptacionReclamoDoc",
            issuer_vat,
            document_code,
            folio,
            accionDoc=action,
        )

    def list_events(self, company, issuer_vat, document_code, folio):
        """List the acceptance, claim and receipt events registered on a DTE."""
        return self._call(
            company, "listarEventosHistDoc", issuer_vat, document_code, folio
        )

    def _call(self, company, operation, issuer_vat, document_code, folio, **extra):
        number, verifier = normalize_rut(issuer_vat).split("-")
        auth = self.authentication_factory()
        try:
            token = auth.token(company)
        finally:
            auth.close()
        body = self._envelope(
            operation,
            {
                "rutEmisor": number,
                "dvEmisor": verifier,
                "tipoDoc": str(document_code),
                "folio": str(int(folio)),
                **extra,
            },
        )
        try:
            with self.session.post(
                ENDPOINTS[self.environment],
                data=body,
                headers={
                    "Content-Type": "text/xml; charset=utf-8",
                    "SOAPAction": '""',
                    "Cookie": "TOKEN=" + token,
                },
                timeout=(10, 60),
                allow_redirects=False,
                verify=True,
                stream=True,
            ) as response:
                content = response.raw.read(MAX_RESPONSE_BYTES + 1, decode_content=True)
        except requests.RequestException:
            # The registry is idempotent per action; a lost answer is re-asked.
            raise SiiTransportError(
                _lt("The connection with the SII claim registry failed."),
                dispatched=True,
            ) from None
        if len(content) > MAX_RESPONSE_BYTES:
            raise SiiTransportError(
                _lt("The SII claim registry answer exceeds the allowed size."),
                dispatched=True,
            )
        return parse_claim_response(content, operation)

    @staticmethod
    def _envelope(operation, parameters):
        root = etree.Element(f"{{{SOAP}}}Envelope", nsmap={"soapenv": SOAP})
        body = etree.SubElement(root, f"{{{SOAP}}}Body")
        method = etree.SubElement(
            body, f"{{{SERVICE_NAMESPACE}}}{operation}", nsmap={"ws": SERVICE_NAMESPACE}
        )
        for name, value in parameters.items():
            etree.SubElement(method, name).text = value
        return etree.tostring(root, encoding="UTF-8", xml_declaration=True)
