# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import date

from odoo.exceptions import UserError
from odoo.tests import tagged

from ..services.rcv_csv_parser import MAX_CSV_BYTES, RcvCsvParser
from .common import SUPPLIER_RUT, RcvTestCase

SII_PURCHASE_HEADER = (
    "Nro;Tipo Doc;Tipo Compra;RUT Proveedor;Razon Social;Folio;Fecha Docto;"
    "Fecha Recepcion;Fecha Acuse;Monto Exento;Monto Neto;Monto IVA Recuperable;"
    "Monto Iva No Recuperable;Codigo IVA No Rec.;Monto Total;"
    "Monto Neto Activo Fijo;IVA Activo Fijo;IVA uso Comun"
)


@tagged("post_install", "-at_install")
class TestRcvCsvParser(RcvTestCase):
    def setUp(self):
        super().setUp()
        self.parser = RcvCsvParser(self.env)

    def test_documented_layout(self):
        result = self.parser.parse(
            self._csv(
                f"33,{SUPPLIER_RUT},Fake Supplier,00934,15/08/2026,"
                "1000,0,190,0,0,1190"
            )
        )
        self.assertFalse(result.errors)
        (row,) = result.rows
        self.assertEqual(row["document_type_code"], "33")
        self.assertEqual(row["folio"], "934")
        self.assertEqual(row["counterpart_vat"], SUPPLIER_RUT)
        self.assertEqual(row["document_date"], date(2026, 8, 15))
        self.assertEqual(row["total_amount"], 1190)
        self.assertTrue(row["tax_breakdown_available"])

    def test_sii_portal_layout_with_semicolons_and_latin1(self):
        line = (
            "1;33;Del Giro;76.222.222-1;Proveedor Ficticio Ñandú;77;"
            "15/08/2026;16/08/2026 10:00:00;;0;2000;380;;;2380;;;"
        )
        content = "\n".join((SII_PURCHASE_HEADER, line)).encode("latin-1")
        result = self.parser.parse(content)
        self.assertFalse(result.errors)
        (row,) = result.rows
        self.assertEqual(row["counterpart_name"], "Proveedor Ficticio Ñandú")
        self.assertEqual(row["net_amount"], 2000)
        self.assertEqual(row["vat_amount"], 380)
        self.assertEqual(row["total_amount"], 2380)
        self.assertFalse(row["tax_breakdown_available"])

    def test_invalid_rows_are_reported_with_line_numbers(self):
        result = self.parser.parse(
            self._csv(
                f"33,{SUPPLIER_RUT},A,1,2026-08-01,100,0,19,0,0,119",
                "33,76222222-2,B,2,2026-08-01,100,0,19,0,0,119",
                f"XX,{SUPPLIER_RUT},C,3,2026-08-01,100,0,19,0,0,119",
                f"33,{SUPPLIER_RUT},D,,2026-08-01,100,0,19,0,0,119",
                f"33,{SUPPLIER_RUT},E,5,2026-31-31,100,0,19,0,0,119",
                f"33,{SUPPLIER_RUT},F,6,2026-08-01,10.5,0,19,0,0,119",
                f"33,{SUPPLIER_RUT},G,1,2026-08-01,100,0,19,0,0,119",
                "",
            )
        )
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(len(result.errors), 6)
        for line, error in zip(range(3, 9), result.errors, strict=True):
            self.assertTrue(str(error).startswith(f"Line {line}:"), error)
        self.assertIn("RUT", str(result.errors[0]))
        self.assertIn("more than once", str(result.errors[-1]))

    def test_file_level_errors_reject_the_file(self):
        with self.assertRaisesRegex(UserError, "empty"):
            self.parser.parse(b"")
        with self.assertRaisesRegex(UserError, "10 MB"):
            self.parser.parse(b"x" * (MAX_CSV_BYTES + 1))
        with self.assertRaisesRegex(UserError, "missing these columns"):
            self.parser.parse(b"folio,total\n1,100")
        with self.assertRaisesRegex(UserError, "no document rows"):
            self.parser.parse(self._csv())
