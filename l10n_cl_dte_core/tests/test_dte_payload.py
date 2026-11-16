# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import TransactionCase, tagged

from ..tools.xml_safety import safe_fromstring

SII = "http://www.sii.cl/SiiDte"


@tagged("post_install", "-at_install")
class TestDtePayload(TransactionCase):
    def _select(self, xml, **kwargs):
        from ..tools.dte_payload import select_dte_payload

        return select_dte_payload(safe_fromstring(xml), **kwargs)

    def _xml(self, code="110", payload="Exportaciones"):
        return (
            f'<DTE xmlns="{SII}"><{payload} ID="doc">'
            f"<Encabezado><IdDoc><TipoDTE>{code}</TipoDTE></IdDoc></Encabezado>"
            f"</{payload}></DTE>"
        ).encode()

    def test_selects_existing_and_export_families_without_mutation(self):
        from ..tools.dte_payload import select_dte_payload

        for code in (
            "33",
            "34",
            "39",
            "41",
            "46",
            "52",
            "56",
            "61",
            "110",
            "111",
            "112",
        ):
            with self.subTest(code=code):
                name = "Exportaciones" if code in {"110", "111", "112"} else "Documento"
                root = safe_fromstring(self._xml(code, name))
                self.assertIs(select_dte_payload(root), root[0])
                self.assertEqual(root[0].get("ID"), "doc")
        prefixed = self._xml().replace(b'xmlns="', b'xmlns:s="')
        for name in (b"DTE", b"Exportaciones", b"Encabezado", b"IdDoc", b"TipoDTE"):
            prefixed = prefixed.replace(b"<" + name, b"<s:" + name)
            prefixed = prefixed.replace(b"</" + name, b"</s:" + name)
        self.assertEqual(self._select(prefixed).tag, f"{{{SII}}}Exportaciones")

    def test_optional_allowlist_including_empty(self):
        xml = self._xml()
        self.assertEqual(self._select(xml, allowed_codes={"110"}).get("ID"), "doc")
        for codes in (set(), {"33"}, {"111"}):
            with self.subTest(codes=codes), self.assertRaises(ValueError):
                self._select(xml, allowed_codes=codes)

    def test_rejects_ambiguous_or_mismatched_structure(self):
        xml = self._xml()
        malformed = [
            xml.replace(b"http://www.sii.cl/SiiDte", b"urn:foreign"),
            xml.replace(f' xmlns="{SII}"'.encode(), b""),
            xml.replace(b"<Exportaciones ", b'<Exportaciones xmlns="urn:foreign" '),
            xml.replace(b"<Encabezado>", b'<Encabezado xmlns="urn:foreign">'),
            xml.replace(b"<IdDoc>", b'<IdDoc xmlns="urn:foreign">'),
            xml.replace(b"<TipoDTE>", b'<TipoDTE xmlns="urn:foreign">'),
            xml.replace(b">110<", b">33<"),
            self._xml("110", "Documento"),
            self._xml("999"),
            xml.replace(b">110<", b"><"),
            xml.replace(b"<TipoDTE>110</TipoDTE>", b""),
            xml.replace(b"</TipoDTE>", b"</TipoDTE><TipoDTE>110</TipoDTE>"),
            xml.replace(b"</IdDoc>", b"</IdDoc><IdDoc/>"),
            xml.replace(b"</Encabezado>", b"</Encabezado><Encabezado/>"),
            xml.replace(
                b"</Encabezado>", b'</Encabezado><Encabezado xmlns="urn:foreign"/>'
            ),
            xml.replace(b"</TipoDTE>", b"<Other/></TipoDTE>"),
            xml.replace(b' ID="doc"', b""),
            xml.replace(b'ID="doc"', b'ID=""'),
            xml.replace(b"<DTE ", b'<DTE ID="doc" '),
            xml.replace(b"</Encabezado>", b'</Encabezado><Other ID="doc"/>'),
            xml.replace(b"</DTE>", b'<Documento ID="other"/></DTE>'),
            xml.replace(b"</Exportaciones>", b"<Documento/></Exportaciones>"),
            xml.replace(b"</Exportaciones>", b"<DTE/></Exportaciones>"),
            xml.replace(b"</Exportaciones>", b"<Liquidacion/></Exportaciones>"),
            xml.replace(b"<Exportaciones ", b"<Wrapper><Exportaciones ").replace(
                b"</Exportaciones>", b"</Exportaciones></Wrapper>"
            ),
            f'<DTE xmlns="{SII}"/>'.encode(),
        ]
        for index, content in enumerate(malformed):
            with self.subTest(case=index), self.assertRaises(ValueError):
                self._select(content)
