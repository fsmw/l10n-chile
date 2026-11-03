from lxml import etree

from odoo.addons.l10n_cl_dte_xml.services.xml_composer import SII_NAMESPACE

from .common import DteInvoiceTestCase


class TestDteXmlComposer(DteInvoiceTestCase):
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
