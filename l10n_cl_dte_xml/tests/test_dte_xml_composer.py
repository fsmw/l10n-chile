from lxml import etree

from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_xml.services.xml_composer import (
    SII_NAMESPACE,
    DteXmlComposer,
)

from .common import DteInvoiceTestCase


class TestDteXmlComposer(DteInvoiceTestCase):
    def test_discounted_lines_include_rounded_discount_amount(self):
        _invoice, snapshot, authorization = self.valid_invoice_snapshot("33")
        snapshot["lines"][0].update(
            {
                "quantity": "3",
                "unit_price": "105",
                "discount_percent": "10",
                "subtotal": "284",
            }
        )
        snapshot["totals"].update({"net": "284", "vat": "54", "total": "338"})
        root = etree.fromstring(self.validate_composed_xml(snapshot, authorization))
        detail = root.find(f"{{{SII_NAMESPACE}}}Documento/{{{SII_NAMESPACE}}}Detalle")
        self.assertEqual(detail.findtext(f"{{{SII_NAMESPACE}}}DescuentoPct"), "10")
        self.assertEqual(detail.findtext(f"{{{SII_NAMESPACE}}}DescuentoMonto"), "32")
        self.assertEqual(detail.findtext(f"{{{SII_NAMESPACE}}}MontoItem"), "284")

    def test_discount_amount_uses_decimal_half_up_rounding(self):
        for quantity, price, percent, expected in [
            ("3", "105", "10", 32),
            ("7", "89", "25", 156),
            ("0.5", "5", "20", 1),
            ("1", "2", "10", 0),
        ]:
            with self.subTest(quantity=quantity, price=price, percent=percent):
                self.assertEqual(
                    DteXmlComposer.line_discount_amount(quantity, price, percent),
                    expected,
                )

    def test_type_33_xml_required_fields(self):
        _invoice, snapshot, authorization = self.valid_invoice_snapshot("33")

        root = etree.fromstring(self.validate_composed_xml(snapshot, authorization))

        self.assertEqual(root.findtext(f".//{{{SII_NAMESPACE}}}TipoDTE"), "33")
        self.assertEqual(
            root.findtext(f".//{{{SII_NAMESPACE}}}RUTEmisor"), "76864280-K"
        )
        self.assertEqual(root.findtext(f".//{{{SII_NAMESPACE}}}MntTotal"), "1190")

    def test_type_34_xml_exempt_totals(self):
        _invoice, snapshot, authorization = self.valid_invoice_snapshot(
            "34", taxable=False
        )

        root = etree.fromstring(self.validate_composed_xml(snapshot, authorization))

        self.assertEqual(root.findtext(f".//{{{SII_NAMESPACE}}}TipoDTE"), "34")
        self.assertEqual(root.findtext(f".//{{{SII_NAMESPACE}}}MntExe"), "1000")
        self.assertFalse(root.find(f".//{{{SII_NAMESPACE}}}IVA"))

    def test_reference_code_is_serialized_separately_from_reason(self):
        _invoice, snapshot, authorization = self.valid_invoice_snapshot("33")
        snapshot["references"] = [
            {
                "line_number": 1,
                "document_type": "33",
                "folio": "42",
                "date": "2026-11-06",
                "reference_code": "2",
                "reason": "CORRIGE TEXTO",
            }
        ]

        root = etree.fromstring(self.validate_composed_xml(snapshot, authorization))
        reference = root.find(f".//{{{SII_NAMESPACE}}}Referencia")

        self.assertEqual(reference.findtext(f"{{{SII_NAMESPACE}}}CodRef"), "2")
        self.assertEqual(
            reference.findtext(f"{{{SII_NAMESPACE}}}RazonRef"), "CORRIGE TEXTO"
        )

    def test_invalid_reference_code_is_rejected(self):
        _invoice, snapshot, _authorization = self.valid_invoice_snapshot("33")
        snapshot["references"] = [
            {
                "line_number": 1,
                "document_type": "33",
                "folio": "42",
                "date": "2026-11-06",
                "reference_code": "4",
                "reason": "invalid",
            }
        ]

        with self.assertRaisesRegex(ValidationError, "reference code"):
            DteXmlComposer().compose_unsigned_document(snapshot)

    def test_xml_matches_schema(self):
        _invoice, snapshot, authorization = self.valid_invoice_snapshot("33")

        xml_bytes = self.validate_composed_xml(snapshot, authorization)

        self.assertIn(b"<TED", xml_bytes)
        self.assertIn(b"<FRMT", xml_bytes)

    def test_xml_declares_chilean_encoding(self):
        _invoice, snapshot, authorization = self.valid_invoice_snapshot("33")

        xml_bytes = self.validate_composed_xml(snapshot, authorization)

        self.assertIn(b"ISO-8859-1", xml_bytes[:100])
