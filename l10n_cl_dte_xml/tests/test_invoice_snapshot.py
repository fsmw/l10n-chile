from unittest.mock import patch

from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_xml.services.invoice_snapshot import (
    InvoiceSnapshotService,
)

from .common import DteInvoiceTestCase


class TestInvoiceSnapshot(DteInvoiceTestCase):
    def test_snapshot_same_input_deterministic(self):
        invoice = self.make_invoice()
        authorization = self.validate_authorization()
        service = InvoiceSnapshotService()
        with patch.object(
            service, "_chile_timestamp", return_value="2026-01-15T12:00:00"
        ):
            first = service.capture_invoice(invoice, authorization, 100, "33")
            second = service.capture_invoice(invoice, authorization, 100, "33")

        self.assertEqual(first, second)

    def test_snapshot_contains_invoice_totals_lines(self):
        invoice = self.make_invoice()
        snapshot, _authorization = self.capture(invoice)

        self.assertEqual(snapshot["document_code"], "33")
        self.assertEqual(snapshot["totals"]["net"], "1000")
        self.assertEqual(snapshot["totals"]["vat"], "190")
        self.assertEqual(snapshot["totals"]["total"], "1190")
        self.assertEqual(snapshot["lines"][0]["name"], self.product_a.name)

    def test_snapshot_immutable_after_source_change(self):
        invoice = self.make_invoice()
        snapshot, _authorization = self.capture(invoice)
        original_receiver = snapshot["receiver"]["name"]
        invoice.partner_id.write({"name": "Updated after snapshot"})

        self.assertEqual(snapshot["receiver"]["name"], original_receiver)

    def test_invalid_invoice_fails_before_folio(self):
        invoice = self.make_invoice()
        invoice.partner_id.write({"vat": False})
        folio_model = self.env["l10n_cl.caf.folio"]
        before = folio_model.search_count([])

        with self.assertRaises(ValidationError):
            InvoiceSnapshotService().validate_invoice_source(invoice, "33")

        self.assertEqual(folio_model.search_count([]), before)
