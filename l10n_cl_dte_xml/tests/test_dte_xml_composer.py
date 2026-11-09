from lxml import etree

from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import (
    SII_NAMESPACE,
    DteXmlComposer,
)

from .common import DteInvoiceTestCase


class TestDteXmlComposer(DteInvoiceTestCase):
    def test_taxable_global_discount_does_not_reduce_the_exempt_total(self):
        _invoice, snapshot, authorization = self.valid_invoice_snapshot(
            "33", taxable=False
        )
        snapshot["lines"][0].update(
            {"quantity": "2", "unit_price": "6840", "subtotal": "13680"}
        )
        snapshot["totals"]["exempt"] = "13680"
        snapshot["totals"]["total"] = "13680"
        snapshot["global_discount"] = {
            "movement": "D",
            "description": "Descuento global",
            "value_type": "%",
            "value": "24",
        }
        xml = self.validate_composed_xml(snapshot, authorization)
        DteXmlComposer().validate_document(xml, strict_sii=True)
        root = etree.fromstring(xml)
        exempt = root.find(f".//{{{SII_NAMESPACE}}}MntExe")
        exempt.text = "10397"
        root.remove(root.xpath("./*[local-name()='Signature']")[0])
        incorrect = SignatureService().sign_document(
            self.company.with_user(self.dte_manager), "demo", etree.tostring(root)
        )
        with self.assertRaisesRegex(ValidationError, "exempt total"):
            DteXmlComposer().validate_document(incorrect, strict_sii=True)

    def test_exempt_adjustment_indicator_is_applied_only_to_exempt_total(self):
        _invoice, snapshot, authorization = self.valid_invoice_snapshot(
            "33", taxable=False
        )
        snapshot["lines"][0].update(
            {"quantity": "2", "unit_price": "6840", "subtotal": "13680"}
        )
        snapshot["totals"]["exempt"] = "10397"
        snapshot["totals"]["total"] = "10397"
        snapshot["global_discount"] = {
            "movement": "D",
            "description": "Exempt adjustment",
            "value_type": "%",
            "value": "24",
        }
        root = etree.fromstring(self.validate_composed_xml(snapshot, authorization))
        adjustment = root.find(f".//{{{SII_NAMESPACE}}}DscRcgGlobal")
        etree.SubElement(adjustment, f"{{{SII_NAMESPACE}}}IndExeDR").text = "1"
        DteXmlComposer()._validate_exempt_total(root)

    def test_discounted_lines_include_the_clp_discount_amount(self):
        _invoice, snapshot, authorization = self.valid_invoice_snapshot("33")
        snapshot["lines"][0].update(
            {
                "quantity": "816",
                "unit_price": "6303",
                "discount_percent": "10",
                "subtotal": "4628923",
            }
        )
        root = etree.fromstring(self.validate_composed_xml(snapshot, authorization))
        detail = root.find(f"{{{SII_NAMESPACE}}}Documento/{{{SII_NAMESPACE}}}Detalle")
        self.assertEqual(detail.findtext(f"{{{SII_NAMESPACE}}}DescuentoPct"), "10")
        self.assertEqual(
            detail.findtext(f"{{{SII_NAMESPACE}}}DescuentoMonto"), "514325"
        )
        self.assertEqual(detail.findtext(f"{{{SII_NAMESPACE}}}MontoItem"), "4628923")

    def test_discount_rounding_uses_the_percentage_without_changing_the_subtotal(self):
        self.assertEqual(
            DteXmlComposer.line_discount_amount("299", "6303", "10"), 188460
        )
        self.assertEqual(
            DteXmlComposer.line_discount_amount("762", "5352", "25"), 1019556
        )
        self.assertEqual(DteXmlComposer.line_discount_amount("0.5", "3", "10"), 0)

    def test_sii_discount_validation_rejects_missing_or_wrong_amount(self):
        _invoice, snapshot, authorization = self.valid_invoice_snapshot("33")
        snapshot["lines"][0]["discount_percent"] = "10"
        root = etree.fromstring(self.validate_composed_xml(snapshot, authorization))
        amount = root.find(f".//{{{SII_NAMESPACE}}}DescuentoMonto")
        amount.text = "999"
        with self.assertRaisesRegex(ValidationError, "discount amount"):
            DteXmlComposer()._validate_line_discounts(root)
        amount.getparent().remove(amount)
        with self.assertRaisesRegex(ValidationError, "discount amount"):
            DteXmlComposer()._validate_line_discounts(root)

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

    def test_xml_matches_schema(self):
        _invoice, snapshot, authorization = self.valid_invoice_snapshot("33")

        xml_bytes = self.validate_composed_xml(snapshot, authorization)

        self.assertIn(b"<TED", xml_bytes)
        self.assertIn(b"<FRMT", xml_bytes)

    def test_xml_declares_chilean_encoding(self):
        _invoice, snapshot, authorization = self.valid_invoice_snapshot("33")

        xml_bytes = self.validate_composed_xml(snapshot, authorization)

        self.assertIn(b"ISO-8859-1", xml_bytes[:100])
