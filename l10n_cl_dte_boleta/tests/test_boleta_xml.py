from decimal import Decimal
from unittest.mock import patch

from lxml import etree

from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_boleta.services.boleta_snapshot import (
    BoletaSnapshotService,
)
from odoo.addons.l10n_cl_dte_boleta.services.boleta_xml_composer import (
    BoletaXmlComposer,
)
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase


class TestBoletaXml(DteInvoiceTestCase):
    def _snapshot(self, code, exempt=False):
        invoice = self.make_invoice(code, taxable=not exempt)
        service = BoletaSnapshotService()
        caf = self.validate_authorization(
            document_code=code,
            folio_start=500 if code == "39" else 510,
            folio_end=503 if code == "39" else 513,
        )
        with patch.object(
            service, "_annual_135_uf_limit", return_value=Decimal("999999999")
        ):
            snapshot = service.capture_invoice(invoice, caf, caf.folio_start, code)
        snapshot["ted_xml"] = BoletaXmlComposer().compose_ted(snapshot, caf)
        return invoice, caf, snapshot

    def test_type_39_xml_has_receipt_fields(self):
        _invoice, _caf, snapshot = self._snapshot("39")
        root = etree.fromstring(BoletaXmlComposer().compose_unsigned_document(snapshot))
        ns = {"sii": "http://www.sii.cl/SiiDte"}
        self.assertEqual(root.findtext(".//sii:TipoDTE", namespaces=ns), "39")
        self.assertEqual(root.findtext(".//sii:IndServicio", namespaces=ns), "3")
        self.assertEqual(root.findtext(".//sii:IndMntNeto", namespaces=ns), "2")
        self.assertIsNotNone(root.find(".//sii:TED", namespaces=ns))

    def test_type_41_xml_has_exempt_amounts(self):
        _invoice, _caf, snapshot = self._snapshot("41", exempt=True)
        xml_bytes = BoletaXmlComposer().compose_unsigned_document(snapshot)
        root = etree.fromstring(xml_bytes)
        ns = {"sii": "http://www.sii.cl/SiiDte"}
        self.assertEqual(root.findtext(".//sii:TipoDTE", namespaces=ns), "41")
        self.assertEqual(
            root.findtext(".//sii:MntExe", namespaces=ns), snapshot["totals"]["exempt"]
        )
        self.assertIsNone(root.find(".//sii:IVA", namespaces=ns))
        self.assertEqual(root.findtext(".//sii:Detalle/sii:IndExe", namespaces=ns), "1")

    def test_invalid_boleta_fails_before_folio(self):
        invoice = self.make_invoice("39", post=False)
        service = BoletaSnapshotService()
        folio_count = self.env["l10n_cl.caf.folio"].search_count([])
        with self.assertRaisesRegex(ValidationError, "posted customer invoices"):
            service.validate_invoice_source(invoice, "39")
        self.assertEqual(self.env["l10n_cl.caf.folio"].search_count([]), folio_count)
