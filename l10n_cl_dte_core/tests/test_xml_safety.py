# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import io
import time

from lxml import etree

from odoo.tests import TransactionCase, tagged

from ..tools.xml_safety import (
    UnsafeXMLError,
    safe_fromstring,
    safe_parse,
    safe_xml_parser,
    safe_xml_schema,
)

XXE_FILE = b"""<?xml version="1.0"?>
<!DOCTYPE r [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<r>&xxe;</r>"""

BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE r [
<!ENTITY a "aaaaaaaaaa">
<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
<!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">
<!ENTITY e "&d;&d;&d;&d;&d;&d;&d;&d;&d;&d;">
<!ENTITY f "&e;&e;&e;&e;&e;&e;&e;&e;&e;&e;">
<!ENTITY g "&f;&f;&f;&f;&f;&f;&f;&f;&f;&f;">
]>
<r>&g;</r>"""

PARAMETER_ENTITY = b"""<?xml version="1.0"?>
<!DOCTYPE r [<!ENTITY % p SYSTEM "file:///etc/passwd"> %p;]>
<r/>"""

EXTERNAL_DTD = b"""<?xml version="1.0"?>
<!DOCTYPE r SYSTEM "http://127.0.0.1:9/never.dtd">
<r>plain</r>"""

SCHEMA = b"""<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
<xs:element name="r" type="xs:integer"/>
</xs:schema>"""


@tagged("post_install", "-at_install")
class TestXmlSafety(TransactionCase):
    def test_parses_regular_namespaced_xml(self):
        root = safe_fromstring(
            b'<DTE xmlns="http://www.sii.cl/SiiDte"><Documento ID="T33F1"/></DTE>'
        )
        node = root.find("{http://www.sii.cl/SiiDte}Documento")
        self.assertEqual(node.get("ID"), "T33F1")

    def test_rejects_external_file_entity(self):
        with self.assertRaises(UnsafeXMLError) as caught:
            safe_fromstring(XXE_FILE)
        self.assertIsInstance(caught.exception, ValueError)
        self.assertNotIn("root:", str(caught.exception))

    def test_rejects_entity_expansion_bomb_quickly(self):
        started = time.monotonic()
        # libxml2 may abort the expansion loop itself; both are rejections.
        with self.assertRaises(etree.XMLSyntaxError):
            safe_fromstring(BILLION_LAUGHS)
        self.assertLess(time.monotonic() - started, 2)

    def test_parameter_entity_is_never_loaded(self):
        with self.assertRaises(etree.XMLSyntaxError):
            safe_fromstring(PARAMETER_ENTITY)

    def test_external_dtd_is_not_fetched(self):
        root = safe_fromstring(EXTERNAL_DTD)
        self.assertEqual(root.text, "plain")

    def test_unsafe_parser_options_are_refused(self):
        for option in ("resolve_entities", "no_network", "load_dtd", "huge_tree"):
            with self.assertRaises(TypeError):
                safe_xml_parser(**{option: True})

    def test_parser_forces_safe_flags(self):
        parser = safe_xml_parser(remove_blank_text=True)
        root = etree.fromstring(XXE_FILE, parser)
        self.assertNotIn("root:", "".join(root.itertext()))

    def test_encoding_option_is_honoured(self):
        root = safe_fromstring("<r>Ñuñoa</r>".encode("latin-1"), encoding="ISO-8859-1")
        self.assertEqual(root.text, "Ñuñoa")

    def test_safe_parse_rejects_entities_from_file_objects(self):
        tree = safe_parse(io.BytesIO(b"<r><a/></r>"))
        self.assertEqual(tree.getroot().tag, "r")
        with self.assertRaises(etree.XMLSyntaxError):
            safe_parse(io.BytesIO(BILLION_LAUGHS))

    def test_schema_is_compiled_with_safe_parser(self):
        schema = safe_xml_schema(io.BytesIO(SCHEMA))
        self.assertTrue(schema.validate(safe_fromstring(b"<r>12</r>")))
        self.assertFalse(schema.validate(safe_fromstring(b"<r>x</r>")))
