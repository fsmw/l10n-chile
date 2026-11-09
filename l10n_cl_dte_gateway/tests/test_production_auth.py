# Copyright 2026 Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import patch

from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase

from odoo.addons.l10n_cl_dte_gateway.services.certification_auth import (
    SiiTransportError,
)
from odoo.addons.l10n_cl_dte_gateway.services.production_response_parser import (
    parse_status,
    parse_upload,
)
from odoo.addons.l10n_cl_dte_gateway.services.sii_authentication import (
    ProductionSiiAuthentication,
)


class TestProductionAuthenticationAndResponses(TransactionCase):
    """Exercise production authentication and response parsing without network I/O."""

    def test_authentication_uses_fixed_host_and_rejects_other_paths(self):
        class Session:
            trust_env = True

            def __init__(self):
                self.headers = {}
                self.cookies = type("Cookies", (), {"clear": lambda self: None})()
                self.closed = False

            def close(self):
                self.closed = True

        session = Session()
        auth = ProductionSiiAuthentication(session=session)
        self.assertEqual(auth.base_url, "https://palena.sii.cl")
        self.assertFalse(session.trust_env)
        with self.assertRaises(SiiTransportError):
            auth.post("https://attacker.invalid/collect")
        auth.close()
        self.assertTrue(session.closed)

    def test_soap_uses_official_production_namespace(self):
        class Session:
            headers = {}
            cookies = type("Cookies", (), {"clear": lambda self: None})()

            def close(self):
                return None

        auth = ProductionSiiAuthentication(session=Session())
        with patch.object(auth, "post", return_value=b"<response/>") as post:
            auth.soap("CrSeed", "getSeed", {})
        request = etree.fromstring(post.call_args.kwargs["data"])
        method = request.xpath("//*[local-name()='getSeed']")[0]
        self.assertEqual(method.tag, "{https://palena.sii.cl/DTEWS/CrSeed.jws}getSeed")
        self.assertEqual(post.call_args.args[0], "/DTEWS/CrSeed.jws")

    def test_upload_receipt_requires_expected_identity_and_track_id(self):
        valid = b"""<RECEPCIONDTE><STATUS>0</STATUS><TRACKID>12345</TRACKID>
          <RUTCOMPANY>60803000-K</RUTCOMPANY><RUTSENDER>60803000-K</RUTSENDER>
          </RECEPCIONDTE>"""
        result = parse_upload(valid, "60803000-K", "60803000-K")
        self.assertEqual(result["state"], "submitted")
        self.assertEqual(result["track_id"], "12345")
        self.assertEqual(result["raw_response"], valid)
        with self.assertRaises(ValidationError):
            parse_upload(valid, "60803000-K", "12345678-5")

    def test_status_parser_checks_track_id_and_reconciles_totals(self):
        inner = (
            "<getEstUpReturn><RESPUESTA><TRACKID>12345</TRACKID><ESTADO>EPR</ESTADO>"
            "<TIPO_DOCTO>33</TIPO_DOCTO><INFORMADOS>1</INFORMADOS>"
            "<ACEPTADOS>1</ACEPTADOS><RECHAZADOS>0</RECHAZADOS><REPAROS>0</REPAROS>"
            "</RESPUESTA></getEstUpReturn>"
        )
        root = etree.Element("{http://schemas.xmlsoap.org/soap/envelope/}Envelope")
        body = etree.SubElement(root, "{http://schemas.xmlsoap.org/soap/envelope/}Body")
        response = etree.SubElement(body, "getEstUpResponse")
        etree.SubElement(response, "getEstUpReturn").text = inner
        result = parse_status(etree.tostring(root), "12345")
        self.assertEqual(result["state"], "accepted")
        self.assertEqual(result["counts"][0]["ACEPTADOS"], 1)
        with self.assertRaises(ValidationError):
            parse_status(etree.tostring(root), "54321")

    def test_status_parser_rejects_inconsistent_document_counters(self):
        inner = (
            "<getEstUpReturn><RESPUESTA><TRACKID>12345</TRACKID><ESTADO>EPR</ESTADO>"
            "<TIPO_DOCTO>33</TIPO_DOCTO><INFORMADOS>2</INFORMADOS>"
            "<ACEPTADOS>1</ACEPTADOS><RECHAZADOS>0</RECHAZADOS><REPAROS>0</REPAROS>"
            "</RESPUESTA></getEstUpReturn>"
        )
        root = etree.Element("{http://schemas.xmlsoap.org/soap/envelope/}Envelope")
        body = etree.SubElement(root, "{http://schemas.xmlsoap.org/soap/envelope/}Body")
        response = etree.SubElement(body, "getEstUpResponse")
        etree.SubElement(response, "getEstUpReturn").text = inner
        with self.assertRaises(ValidationError):
            parse_status(etree.tostring(root), "12345")
