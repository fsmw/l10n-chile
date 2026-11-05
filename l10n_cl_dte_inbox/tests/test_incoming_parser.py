# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase

from ..services.incoming_parser import parse_incoming_dte


class TestIncomingDteParser(TransactionCase):
    def test_parses_identity_from_single_dte(self):
        parsed = parse_incoming_dte(self._xml())
        self.assertEqual(parsed["document_code"], "33")
        self.assertEqual(parsed["folio"], 12)
        self.assertEqual(parsed["issue_date"], "2026-10-15")
        self.assertEqual(parsed["issuer_vat"], "76000000-0")
        self.assertEqual(parsed["receiver_vat"], "66666666-6")

    def test_rejects_malformed_xml(self):
        with self.assertRaises(ValidationError):
            parse_incoming_dte(b"<DTE>", translate=str)

    def test_rejects_multiple_documents_in_one_import(self):
        xml = b"<EnvioDTE>" + self._xml() + self._xml() + b"</EnvioDTE>"
        with self.assertRaises(ValidationError):
            parse_incoming_dte(xml, translate=str)

    def test_disables_external_entity_resolution(self):
        xml = b"""<!DOCTYPE DTE [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>
        <DTE>&xxe;</DTE>"""
        with self.assertRaises(ValidationError):
            parse_incoming_dte(xml, translate=str)

    @staticmethod
    def _xml():
        return b"""<?xml version="1.0" encoding="ISO-8859-1"?>
        <DTE xmlns="http://www.sii.cl/SiiDte">
          <Documento ID="T33F12">
            <Encabezado>
              <IdDoc><TipoDTE>33</TipoDTE><Folio>12</Folio><FchEmis>2026-10-15</FchEmis></IdDoc>
              <Emisor><RUTEmisor>76000000-0</RUTEmisor></Emisor>
              <Receptor><RUTRecep>66666666-6</RUTRecep></Receptor>
            </Encabezado>
          </Documento>
        </DTE>"""
