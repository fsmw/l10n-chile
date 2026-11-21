# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from .common import TaxReportsTestCase


@tagged("post_install", "-at_install")
class TestF29Report(TaxReportsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company.write(
            {"l10n_cl_f29_ppm_rate": 1.5, "l10n_cl_f29_proportional_factor": 60.0}
        )
        cls.foreign_customer = cls.env["res.partner"].create(
            {
                "name": "Synthetic Foreign Customer",
                "is_company": True,
                "country_id": cls.env.ref("base.us").id,
                "l10n_cl_sii_taxpayer_type": "4",
            }
        )
        no_tax = cls.env["account.tax"]
        # Sales: two invoices, one receipt, one credit note, exempt and export.
        cls._document("out_invoice", "33", 1000, cls.sale_vat)
        cls._document("out_invoice", "33", 2000, cls.sale_vat)
        cls._document("out_invoice", "39", 500, cls.sale_vat)
        cls._document("out_refund", "61", 200, cls.sale_vat)
        cls._document("out_invoice", "34", 300, no_tax)
        cls._document("out_invoice", "110", 400, no_tax, partner=cls.foreign_customer)
        # Purchases: recoverable, non-recoverable, common use, purchase
        # invoice with total VAT retention and a fee receipt.
        cls._document("in_invoice", "33", 1000, cls.purchase_vat)
        cls._document("in_invoice", "33", 400, cls.purchase_vat_not_recoverable)
        cls._document("in_invoice", "33", 500, cls.purchase_vat_common_use)
        cls._document(
            "in_invoice", "46", 1000, cls.purchase_vat | cls.vat_total_retention
        )
        cls._document(
            "in_invoice", "71", 2000, cls.second_category, partner=cls.fee_issuer
        )
        # Another month: must be ignored.
        cls._document("out_invoice", "33", 9000, cls.sale_vat, date="2026-09-02")

    def _report(self, **values):
        report = self.env["l10n_cl.f29.report"].create(
            {"company_id": self.company.id, "year": 2026, "month": "8"} | values
        )
        report.action_compute()
        return report

    def _box(self, report, xmlid):
        box = self.env.ref(f"l10n_cl_dte_tax_reports.f29_box_{xmlid}")
        line = report.line_ids.filtered(lambda line: line.box_id == box)
        return line.count, line.amount

    def test_period_and_defaults(self):
        report = self._report()
        self.assertEqual(str(report.date_from), "2026-08-01")
        self.assertEqual(str(report.date_to), "2026-08-31")
        self.assertEqual(report.ppm_rate, 1.5)
        self.assertEqual(report.proportional_factor, 60.0)

    def test_sales_boxes(self):
        report = self._report()
        self.assertEqual(self._box(report, "sale_invoice"), (2, 570))
        self.assertEqual(self._box(report, "sale_receipt"), (1, 95))
        self.assertEqual(self._box(report, "sale_credit_note"), (1, 38))
        self.assertEqual(self._box(report, "sale_debit_note"), (0, 0))
        self.assertEqual(self._box(report, "sale_exempt"), (1, 300))
        self.assertEqual(self._box(report, "sale_export"), (1, 400))
        self.assertEqual(report.total_debits, 570 + 95 - 38)

    def test_purchase_boxes(self):
        report = self._report()
        self.assertEqual(self._box(report, "purchase_invoice"), (2, 380))
        self.assertEqual(self._box(report, "purchase_common_use")[1], 57)
        self.assertEqual(self._box(report, "purchase_not_recoverable"), (1, 400))
        self.assertEqual(self._box(report, "purchase_credit_note"), (0, 0))
        self.assertEqual(report.total_credits, 380 + 57)

    def test_withholdings_and_ppm(self):
        report = self._report()
        self.assertEqual(self._box(report, "withholding_vat_total")[1], 190)
        self.assertEqual(self._box(report, "withholding_second_category")[1], 305)
        self.assertEqual(report.total_withholdings, 495)
        self.assertEqual(report.ppm_base, 1000 + 2000 + 500 - 200 + 300 + 400)
        self.assertEqual(report.ppm_amount, 60)

    def test_vat_payable(self):
        report = self._report()
        self.assertEqual(report.vat_payable, 627 - 437)
        self.assertEqual(report.credit_remainder, 0)
        self.assertEqual(report.total_payable, 190 + 495 + 60)
        total_line = report.line_ids.filtered(lambda line: line.amount_code == "89")
        self.assertEqual(total_line.amount, 190)

    def test_remaining_credit(self):
        report = self._report(previous_credit_remainder=1000)
        self.assertEqual(report.total_credits, 1437)
        self.assertEqual(report.vat_payable, 0)
        self.assertEqual(report.credit_remainder, 1437 - 627)
        self.assertEqual(report.total_payable, 495 + 60)

    def test_recompute_and_print(self):
        report = self._report()
        line_count = len(report.line_ids)
        report.action_compute()
        self.assertEqual(len(report.line_ids), line_count)
        html = self.env["ir.actions.report"]._render_qweb_html(
            "l10n_cl_dte_tax_reports.report_f29", report.ids
        )[0]
        self.assertIn(b"F29 Worksheet", html)

    def test_custom_box_configuration(self):
        box = self.env["l10n_cl.f29.box"].create(
            {
                "name": "Custom: recoverable VAT on vendor bills",
                "section": "info",
                "move_scope": "purchase",
                "amount_code": "X1",
                "amount_source": "tax",
                "tag_ids": [
                    (6, 0, self.purchase_vat.invoice_repartition_line_ids.tag_ids.ids)
                ],
                "company_id": self.company.id,
            }
        )
        report = self._report()
        line = report.line_ids.filtered(lambda line: line.box_id == box)
        self.assertEqual(line.amount, 380)
