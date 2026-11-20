# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import MagicMock, Mock

from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase

from ..services.claim_registry import (
    ENDPOINTS,
    SiiClaimRegistryClient,
    parse_claim_response,
)

REGISTER_OK = b"""<?xml version="1.0" encoding="UTF-8"?>
<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body>
<ns2:ingresarAceptacionReclamoDocResponse
 xmlns:ns2="http://ws.registroreclamodte.diii.sdi.sii.cl"><return>
<codResp>0</codResp><descResp>Accion Completada OK</descResp>
</return></ns2:ingresarAceptacionReclamoDocResponse></S:Body></S:Envelope>"""

EVENTS = b"""<?xml version="1.0" encoding="UTF-8"?>
<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body>
<ns2:listarEventosHistDocResponse
 xmlns:ns2="http://ws.registroreclamodte.diii.sdi.sii.cl"><return>
<codResp>15</codResp><descResp>Listado de eventos del documento</descResp>
<listaEventosDoc><codEvento>ACD</codEvento><descEvento>Acepta Contenido del
Documento</descEvento><rutResponsable>76086428</rutResponsable>
<dvResponsable>5</dvResponsable><fechaEvento>2026-01-20 10:00:00</fechaEvento>
</listaEventosDoc></return></ns2:listarEventosHistDocResponse></S:Body>
</S:Envelope>"""

FAULT = b"""<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body>
<S:Fault><faultcode>S:Server</faultcode><faultstring>Token invalido</faultstring>
</S:Fault></S:Body></S:Envelope>"""


class TestClaimRegistry(TransactionCase):
    """The SII acceptance and claim registry client, on recorded answers."""

    def _client(self, content):
        response = MagicMock()
        response.raw.read.return_value = content
        session = Mock()
        session.post.return_value.__enter__ = Mock(return_value=response)
        session.post.return_value.__exit__ = Mock(return_value=False)
        auth = Mock()
        auth.token.return_value = "TOKEN123"
        client = SiiClaimRegistryClient(
            "production", session=session, authentication_factory=lambda: auth
        )
        return client, session

    def test_acceptance_is_registered_with_the_token_cookie(self):
        client, session = self._client(REGISTER_OK)
        result = client.register_action(Mock(), "76.086.428-5", "33", 1234, "ACD")
        self.assertTrue(result["success"])
        self.assertEqual(result["code"], 0)
        url = session.post.call_args.args[0]
        kwargs = session.post.call_args.kwargs
        self.assertEqual(url, ENDPOINTS["production"])
        self.assertEqual(kwargs["headers"]["Cookie"], "TOKEN=TOKEN123")
        body = etree.fromstring(kwargs["data"])
        values = {
            node.tag: node.text
            for node in body.iter()
            if node.tag in {"rutEmisor", "dvEmisor", "tipoDoc", "folio", "accionDoc"}
        }
        self.assertEqual(
            values,
            {
                "rutEmisor": "76086428",
                "dvEmisor": "5",
                "tipoDoc": "33",
                "folio": "1234",
                "accionDoc": "ACD",
            },
        )

    def test_events_are_listed(self):
        client, _session = self._client(EVENTS)
        result = client.list_events(Mock(), "76086428-5", "33", 1234)
        self.assertFalse(result["success"])
        self.assertEqual(result["events"][0]["code"], "ACD")
        self.assertEqual(result["events"][0]["responsible"], "76086428-5")

    def test_unknown_action_fault_and_mode_are_refused(self):
        client, _session = self._client(FAULT)
        with self.assertRaises(ValidationError):
            client.register_action(Mock(), "76086428-5", "33", 1, "XYZ")
        with self.assertRaisesRegex(ValidationError, "Token invalido"):
            client.register_action(Mock(), "76086428-5", "33", 1, "RCD")
        with self.assertRaises(ValidationError):
            SiiClaimRegistryClient("demo")
        with self.assertRaises(ValidationError):
            parse_claim_response(REGISTER_OK, "listarEventosHistDoc")
