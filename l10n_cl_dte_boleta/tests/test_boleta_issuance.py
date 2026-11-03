from decimal import Decimal
from unittest.mock import patch

from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_boleta.services.boleta_snapshot import (
    BoletaSnapshotService,
)
from odoo.addons.l10n_cl_dte_boleta.services.boleta_xml_composer import (
    BoletaXmlComposer,
)
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase


class TestBoletaIssuance(DteInvoiceTestCase):
    def setUp(self):
        super().setUp()
        self.service = BoletaSnapshotService()

    def _capture(self, invoice, code):
        caf = self.validate_authorization(
            document_code=code, folio_start=410, folio_end=415
        )
        with patch.object(
            self.service, "_annual_135_uf_limit", return_value=Decimal("999999999")
        ):
            return self.service.capture_invoice(invoice, caf, 410, code)

    def test_type_39_accepts_anonymous_consumer(self):
        consumer = self.env["res.partner"].create(
            {
                "name": "Consumidor Final",
                "vat": "66666666-6",
                "country_id": self.chile.id,
            }
        )
        invoice = self.make_invoice("39", partner=consumer)
        snapshot = self._capture(invoice, "39")
        self.assertEqual(snapshot["receiver"]["vat"], "66666666-6")
        self.assertEqual(snapshot["document_code"], "39")

    def test_type_41_uses_exempt_rules(self):
        invoice = self.make_invoice("41", taxable=False)
        snapshot = self._capture(invoice, "41")
        self.assertIsNone(snapshot["totals"]["net"])
        self.assertEqual(snapshot["totals"]["exempt"], str(int(invoice.amount_untaxed)))
        self.assertTrue(all(line["exempt"] for line in snapshot["lines"]))
        taxable = self.make_invoice("41", taxable=True)
        with self.assertRaisesRegex(ValidationError, "requires all lines to be exempt"):
            authorization = self.env["l10n_cl.caf.authorization"].search(
                [("document_code", "=", "41")], limit=1
            )
            self.service.capture_invoice(taxable, authorization, 410, "41")

    def test_payment_method_required_and_serialized(self):
        invoice = self.make_invoice("39")
        with patch.object(
            self.service, "_annual_135_uf_limit", return_value=Decimal("1")
        ):
            with self.assertRaisesRegex(ValidationError, "Choose the payment method"):
                self.service.validate_invoice_source(invoice, "39")
            invoice.dte_boleta_payment_method = "2"
            self.service.validate_invoice_source(invoice, "39")
        with patch.object(
            self.service, "_annual_135_uf_limit", return_value=Decimal("1")
        ):
            caf = self.validate_authorization(
                document_code="39", folio_start=410, folio_end=415
            )
            snapshot = self.service.capture_invoice(invoice, caf, 410, "39")
        snapshot["ted_xml"] = BoletaXmlComposer().compose_ted(snapshot, caf)
        self.assertEqual(snapshot["payment_method"], "2")
        xml_bytes = BoletaXmlComposer().compose_unsigned_document(snapshot)
        self.assertIn(b"<MedioPago>2</MedioPago>", xml_bytes)

    def test_boleta_demo_flow_keeps_evidence(self):
        invoice = self.make_invoice("39")
        adapter = invoice._get_dte_issuance_adapter("39")
        self.assertEqual(adapter.supported_document_codes, {"39", "41"})
        caf = self.validate_authorization(
            document_code="39", folio_start=410, folio_end=415
        )
        with patch.object(
            self.service, "_annual_135_uf_limit", return_value=Decimal("999999999")
        ):
            snapshot = self.service.capture_invoice(invoice, caf, 410, "39")
        snapshot["ted_xml"] = adapter.compose_ted(snapshot, caf)
        xml_bytes = adapter.compose_unsigned_document(snapshot)
        self.assertIn(b"<TipoDTE>39</TipoDTE>", xml_bytes)
        self.assertEqual(snapshot["folio"], 410)
