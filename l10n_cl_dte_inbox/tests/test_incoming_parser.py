# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase

from ..services.incoming_parser import extract_incoming_dtes, parse_incoming_dte


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

    def test_rejects_dtd_in_utf16_input(self):
        xml = (
            '<?xml version="1.0" encoding="UTF-16"?>'
            '<!DOCTYPE DTE [<!ENTITY a "test">]><DTE>&a;</DTE>'
        ).encode("utf-16")
        with self.assertRaises(ValidationError):
            parse_incoming_dte(xml, translate=str)

    def test_rejects_foreign_wrapper_and_namespace(self):
        for content in (
            b"<foreign>" + self._xml().split(b"?>", 1)[1] + b"</foreign>",
            self._xml().replace(
                b"http://www.sii.cl/SiiDte", b"https://example.invalid"
            ),
        ):
            with self.subTest(content=content), self.assertRaises(ValidationError):
                parse_incoming_dte(content, translate=str)

    def test_rejects_repeated_xml_ids(self):
        xml = self._xml().replace(b"<IdDoc>", b'<IdDoc ID="T33F12">')
        with self.assertRaises(ValidationError):
            parse_incoming_dte(xml, translate=str)

    def test_nested_export_wrapper_yields_original_envelope(self):
        envelope, documents = extract_incoming_dtes(self._nested(), str)

        self.assertEqual(envelope.find(self._sii("SetDTE")).get("ID"), "SetDTE-inner")
        self.assertIsNotNone(envelope.getparent())
        self.assertEqual(
            [node.find(self._sii("Documento")).get("ID") for node in documents],
            ["T33F12"],
        )

    def test_plain_envelope_is_not_treated_as_wrapped(self):
        plain = self._envelope(self._dte()).replace(
            b"<EnvioDTE ", b'<EnvioDTE xmlns="http://www.sii.cl/SiiDte" ', 1
        )
        envelope, documents = extract_incoming_dtes(plain, str)

        self.assertIsNone(envelope.getparent())
        self.assertEqual(len(documents), 1)

    def test_malformed_nested_wrappers_are_rejected(self):
        inner = self._envelope(self._dte(), set_id=b"SetDTE-inner")
        for content in (
            # Two original envelopes in one wrapper.
            self._nested(inner + inner.replace(b"SetDTE-inner", b"SetDTE-two")),
            # A DTE beside the original envelope.
            self._nested(inner + self._dte().replace(b"T33F12", b"T33F13")),
            # An unknown element beside the original envelope.
            self._nested(inner + b"<Extra/>"),
            # More than one level of wrapping.
            self._nested(self._envelope(inner, set_id=b"SetDTE-middle")),
        ):
            with self.subTest(content=content), self.assertRaises(ValidationError):
                extract_incoming_dtes(content, str)

    def test_nested_boleta_envelopes_are_not_unwrapped(self):
        content = self._nested().replace(b"EnvioDTE", b"EnvioBOLETA")
        with self.assertRaises(ValidationError):
            extract_incoming_dtes(content, str)

    @staticmethod
    def _sii(tag):
        return f"{{http://www.sii.cl/SiiDte}}{tag}"

    @classmethod
    def _dte(cls):
        return (
            cls._xml()
            .split(b"?>", 1)[1]
            .replace(b' xmlns="http://www.sii.cl/SiiDte"', b"", 1)
        )

    @staticmethod
    def _envelope(body, set_id=b"SetDTE-inner"):
        return (
            b'<EnvioDTE version="1.0"><SetDTE ID="' + set_id + b'">'
            b"<Caratula/>" + body + b"</SetDTE></EnvioDTE>"
        )

    @classmethod
    def _nested(cls, body=None):
        body = cls._envelope(cls._dte()) if body is None else body
        return (
            b'<EnvioDTE xmlns="http://www.sii.cl/SiiDte" version="1.0">'
            b'<SetDTE ID="SetDTE-outer"><Caratula/>' + body + b"</SetDTE></EnvioDTE>"
        )

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
