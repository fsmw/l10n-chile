# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from .common import DteReportTestCase


@tagged("post_install", "-at_install")
class TestInvoiceReport(DteReportTestCase):
    """The standard invoice PDF carries the SII stamp and the DTE folio."""

    def _render(self, invoice):
        html, _format = self.env["ir.actions.report"]._render_qweb_html(
            "account.report_invoice", invoice.ids
        )
        return html.decode()

    def test_issued_invoice_prints_the_stamp_and_the_sii_folio(self):
        document = self.create_signed_document(folio_start=730)
        invoice = document.source_move_id
        html = self._render(invoice)
        self.assertIn('name="dte_stamp"', html)
        self.assertIn("data:image/png;base64,", html)
        self.assertIn("Timbre Electrónico SII", html)
        self.assertIn(str(document.folio), html)
        values = invoice._get_dte_print_values()
        self.assertEqual(values["folio"], document.folio)
        self.assertTrue(values["is_demo"])

    def test_invoice_without_dte_prints_without_stamp(self):
        invoice = self.make_invoice()
        self.assertEqual(invoice._get_dte_print_values(), {})
        self.assertNotIn('name="dte_stamp"', self._render(invoice))
