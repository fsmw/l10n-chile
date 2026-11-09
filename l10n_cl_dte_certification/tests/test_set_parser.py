# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import TransactionCase, tagged

from ..services.set_parser import CertificationSetParser


@tagged("post_install", "-at_install")
class TestCertificationSetParser(TransactionCase):
    def test_v2_interprets_all_certification_note_operations_from_source(self):
        source = (
            b"SET DE PRUEBAS - NUMERO DE ATENCION: 12345\n"
            b"CASO 12345-1\n"
            b"DOCUMENTO FACTURA ELECTRONICA\n"
            b"ITEM\tCANTIDAD\tPRECIO UNITARIO\n"
            b"ITEM 1\tProducto A\t2\t1000\n"
            b"CASO 12345-2\n"
            b"DOCUMENTO NOTA DE CREDITO ELECTRONICA\n"
            b"REFERENCIA FACTURA ELECTRONICA CORRESPONDIENTE A CASO 12345-1\n"
            b"RAZON REFERENCIA: CORRIGE GIRO DEL RECEPTOR\n"
            b"DICE: Giro antiguo\n"
            b"DEBE DECIR: Giro corregido\n"
            b"CASO 12345-3\n"
            b"DOCUMENTO FACTURA ELECTRONICA\n"
            b"ITEM\tCANTIDAD\tPRECIO UNITARIO\n"
            b"ITEM 1\tProducto B\t3\t500\n"
            b"CASO 12345-4\n"
            b"DOCUMENTO NOTA DE CREDITO ELECTRONICA\n"
            b"REFERENCIA FACTURA ELECTRONICA CORRESPONDIENTE A CASO 12345-3\n"
            b"RAZON REFERENCIA: DEVOLUCION DE MERCADERIAS\n"
            b"ITEM\tCANTIDAD\n"
            b"ITEM 1\tProducto B\t1\n"
            b"CASO 12345-5\n"
            b"DOCUMENTO FACTURA ELECTRONICA\n"
            b"ITEM\tCANTIDAD\tPRECIO UNITARIO\n"
            b"ITEM 1\tProducto C\t1\t750\n"
            b"CASO 12345-6\n"
            b"DOCUMENTO NOTA DE CREDITO ELECTRONICA\n"
            b"REFERENCIA FACTURA ELECTRONICA CORRESPONDIENTE A CASO 12345-5\n"
            b"RAZON REFERENCIA: ANULA FACTURA\n"
            b"CASO 12345-7\n"
            b"DOCUMENTO NOTA DE DEBITO ELECTRONICA\n"
            b"REFERENCIA NOTA DE CREDITO ELECTRONICA CORRESPONDIENTE A CASO 12345-2\n"
            b"RAZON REFERENCIA: ANULA NOTA DE CREDITO ELECTRONICA\n"
        )

        result = CertificationSetParser().parse(
            source,
            "utf-8",
            "sii_section_v2",
            selected_attention="12345",
        )

        (
            invoice,
            text_note,
            return_invoice,
            return_note,
            cancel_invoice,
            cancel_note,
            debit_note,
        ) = result["cases"]
        self.assertEqual(
            [case["document_code"] for case in result["cases"]],
            ["33", "61", "33", "61", "33", "61", "56"],
        )
        self.assertEqual(text_note["note_operation"], "text")
        self.assertEqual(text_note["reference_reason"], "CORRIGE GIRO DEL RECEPTOR")
        self.assertEqual(
            text_note["sii_reference_reason"],
            "DICE: Giro antiguo\nDEBE DECIR: Giro corregido",
        )
        self.assertEqual(text_note["sii_reference_reason_source_lines"], [10, 11])
        self.assertEqual(return_note["note_operation"], "return")
        self.assertEqual(return_note["lines"][0]["quantity"], "1")
        self.assertIsNone(return_note["lines"][0]["unit_price"])
        self.assertEqual(cancel_note["note_operation"], "cancel")
        self.assertEqual(debit_note["note_operation"], "cancel")
        self.assertEqual(debit_note["reference_case_identifier"], "12345-2")
        self.assertTrue(
            all(not case["unsupported_instructions"] for case in result["cases"])
        )
        self.assertEqual(invoice["document_code"], "33")
        self.assertEqual(return_invoice["document_code"], "33")
        self.assertEqual(cancel_note["document_code"], "61")

    def test_selected_invoice_and_note_cases_are_interpreted(self):
        source = (
            "SET DE PRUEBAS - NUMERO DE ATENCION: 12345\n"
            "Instrucción general de la sección\n"
            "CASO 12345-1\n"
            "DOCUMENTO FACTURA ELECTRONICA\n"
            "ITEM  CANTIDAD  PRECIO UNITARIO\n"
            "ITEM 1  Servicio A  1  1000\n"
            "DESCUENTO GLOBAL 10%\n"
            "CASO 12345-2\n"
            "DOCUMENTO NOTA DE CREDITO ELECTRONICA\n"
            "REFERENCIA: CASO 12345-1\n"
            "RAZON REFERENCIA: CORRIGE GIRO\n"
            "CASO 12345-3\n"
            "DOCUMENTO NOTA DE DEBITO ELECTRONICA\n"
            "REFERENCIA: CASO 12345-2\n"
            "RAZON REFERENCIA: MODIFICA MONTO\n"
        ).encode()

        result = CertificationSetParser().parse(
            source,
            "utf-8",
            "sii_section_v1",
            selected_attention="12345",
        )

        invoice, note, debit = result["cases"]
        self.assertEqual(invoice["document_code"], "33")
        self.assertEqual(invoice["global_discount_percent"], "10")
        self.assertEqual(invoice["lines"][0]["quantity"], "1")
        self.assertFalse(invoice["unsupported_instructions"])
        self.assertEqual(note["document_code"], "56")
        self.assertEqual(note["reference_case_identifier"], "12345-1")
        self.assertEqual(note["note_operation"], "text")
        self.assertFalse(note["unsupported_instructions"])
        self.assertEqual(debit["document_code"], "61")
        self.assertEqual(debit["reference_case_identifier"], "12345-2")

    def test_note_detail_can_inherit_missing_value_from_reference(self):
        source = (
            b"SET DE PRUEBAS - NUMERO DE ATENCION: 12345\n"
            b"CASO 12345-1\n"
            b"DOCUMENTO FACTURA ELECTRONICA\n"
            b"ITEM  CANTIDAD  PRECIO UNITARIO\n"
            b"ITEM 1  Servicio A  1  1000\n"
            b"CASO 12345-2\n"
            b"DOCUMENTO NOTA DE CREDITO ELECTRONICA\n"
            b"REFERENCIA: CASO 12345-1\n"
            b"RAZON REFERENCIA: DEVOLUCION DE MERCADERIAS\n"
            b"ITEM  CANTIDAD\n"
            b"Servicio A  1\n"
        )

        result = CertificationSetParser().parse(
            source,
            "utf-8",
            "sii_section_v1",
            selected_attention="12345",
        )
        note = result["cases"][1]
        self.assertEqual(note["note_operation"], "return")
        self.assertEqual(note["lines"][0]["quantity"], "1")
        self.assertIsNone(note["lines"][0]["unit_price"])
        self.assertFalse(note["unsupported_instructions"])
