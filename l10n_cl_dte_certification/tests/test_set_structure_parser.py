# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hashlib

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from ..services.set_structure_parser import (
    PARSER_VERSION,
    CertificationSetStructureParser,
)


@tagged("post_install", "-at_install")
class TestCertificationSetStructureParser(TransactionCase):
    def setUp(self):
        super().setUp()
        self.parser = CertificationSetStructureParser(self.env)

    def test_parses_multiple_sections_and_preserves_case_lines(self):
        source = (
            b"SET A - NUMERO DE ATENCION: 123\n"
            b"CASO 123-1\nDOCUMENTO FACTURA ELECTRONICA\nDetalle\n"
            b"SET B - NUMERO DE ATENCION: 456\n"
            b"CASO 456-1\nDOCUMENTO NOTA DE DEBITO ELECTRONICA\n"
        )

        result = self.parser.parse(source)

        self.assertEqual(result["parser_version"], PARSER_VERSION)
        self.assertEqual(result["source_digest"], hashlib.sha256(source).hexdigest())
        self.assertEqual(
            [s["attention_number"] for s in result["sections"]], ["123", "456"]
        )
        first_case = result["sections"][0]["cases"][0]
        self.assertEqual(first_case["identifier"], "123-1")
        self.assertEqual(first_case["document_code"], "33")
        self.assertEqual(
            first_case["instructions"],
            "CASO 123-1\nDOCUMENTO FACTURA ELECTRONICA\nDetalle",
        )
        self.assertEqual(result["sections"][1]["document_counts"], {"56": 1})

    def test_decodes_legacy_latin1_without_changing_instructions(self):
        source = (
            "SET A - NUMERO DE ATENCION: 123\n"
            "CASO 123-1\nDOCUMENTO FACTURA ELECTRONICA\nDirección: Ñuñoa\n"
        ).encode("iso-8859-1")

        result = self.parser.parse(source)

        self.assertEqual(result["encoding"], "iso-8859-1")
        self.assertIn(
            "Dirección: Ñuñoa",
            result["sections"][0]["cases"][0]["instructions"],
        )

    def test_rejects_duplicate_attention_and_case_identifiers(self):
        repeated_attention = (
            b"SET A - NUMERO DE ATENCION: 123\nCASO 123-1\n"
            b"DOCUMENTO FACTURA ELECTRONICA\nSET B - NUMERO DE ATENCION: 123\n"
            b"CASO 123-2\nDOCUMENTO FACTURA ELECTRONICA\n"
        )
        repeated_case = (
            b"SET A - NUMERO DE ATENCION: 123\nCASO 123-1\n"
            b"DOCUMENTO FACTURA ELECTRONICA\nCASO 123-1\n"
            b"DOCUMENTO FACTURA ELECTRONICA\n"
        )

        with self.assertRaises(ValidationError):
            self.parser.parse(repeated_attention)
        with self.assertRaises(ValidationError):
            self.parser.parse(repeated_case)

    def test_rejects_sections_with_missing_or_ambiguous_document_type(self):
        source = (
            b"SET A - NUMERO DE ATENCION: 123\nCASO 123-1\n"
            b"DOCUMENTO FACTURA ELECTRONICA\nDOCUMENTO NOTA DE CREDITO ELECTRONICA\n"
        )

        with self.assertRaises(ValidationError):
            self.parser.parse(source)

    def test_rejects_non_text_input(self):
        for source in (b"", b"%PDF synthetic", b"<xml/>", b"\x00binary"):
            with self.subTest(source=source), self.assertRaises(ValidationError):
                self.parser.parse(source)

    def test_keeps_unknown_document_types_as_explicit_blockers(self):
        source = (
            b"SET A - NUMERO DE ATENCION: 123\n"
            b"CASO 123-1\nDOCUMENTO TIPO DESCONOCIDO\n"
        )

        result = self.parser.parse(source)

        self.assertEqual(
            result["diagnostics"],
            [
                {
                    "code": "unsupported_document_type",
                    "case": "123-1",
                    "source_line": 3,
                    "source_text": "DOCUMENTO TIPO DESCONOCIDO",
                }
            ],
        )
