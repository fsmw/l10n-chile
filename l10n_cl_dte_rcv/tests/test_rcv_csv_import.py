# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import OTHER_SUPPLIER_RUT, SUPPLIER_RUT, RcvTestCase


@tagged("post_install", "-at_install")
class TestRcvCsvImport(RcvTestCase):
    def _import(self, content, **kwargs):
        action = self._wizard(content, **kwargs).action_import()
        return self.env["l10n_cl.dte.rcv.sync"].browse(action["res_id"])

    def test_import_creates_period_read_documents_and_summary(self):
        content = self._csv(
            f"33,{SUPPLIER_RUT},Fake Supplier,10,2026-08-01,1000,0,190,0,0,1190",
            f"33,{OTHER_SUPPLIER_RUT},Other Fake,11,2026-08-02,500,0,95,0,0,595",
            f"34,{SUPPLIER_RUT},Fake Supplier,12,2026-08-03,0,300,,,,300",
        )
        sync = self._import(content)
        self.assertEqual(sync.source, "csv")
        self.assertEqual(sync.state, "success")
        self.assertEqual(sync.detail_count, 3)
        self.assertEqual(sync.summary_count, 2)
        self.assertEqual(sync.source_file_id.raw, content)
        self.assertEqual(len(sync.response_hash), 64)
        period = sync.period_id
        self.assertEqual((period.year, period.month), (2026, 8))
        self.assertEqual(period.state, "success")
        self.assertEqual(period.current_sync_id, sync)
        self.assertEqual(period.document_count, 3)
        summary = sync.summary_ids.filtered(lambda row: row.document_type_code == "33")
        self.assertEqual(
            (summary.document_count, summary.net_amount, summary.total_amount),
            (2, 1500, 1785),
        )
        self.assertEqual(sync.section_ids.sii_state, "CSV")
        documents = sync.document_ids
        self.assertEqual(set(documents.mapped("source")), {"csv"})
        exempt = documents.filtered(lambda doc: doc.document_type_code == "34")
        self.assertFalse(exempt.tax_breakdown_available)
        self.assertTrue((documents - exempt).mapped("tax_breakdown_available")[0])

    def test_reimport_reuses_period_and_documents(self):
        content = self._csv(
            f"33,{SUPPLIER_RUT},Fake Supplier,10,2026-08-01,1000,0,190,0,0,1190"
        )
        first = self._import(content)
        second = self._import(content)
        self.assertEqual(first.period_id, second.period_id)
        self.assertEqual(first.document_ids | second.document_ids, second.document_ids)
        self.assertEqual(len(second.document_ids.observation_ids), 2)

    def test_invalid_rows_are_recorded_and_mark_the_read_partial(self):
        sync = self._import(
            self._csv(
                f"33,{SUPPLIER_RUT},Fake Supplier,10,2026-08-01,1000,0,190,0,0,1190",
                "33,76222222-9,Bad RUT,11,2026-08-01,1000,0,190,0,0,1190",
            )
        )
        self.assertEqual(sync.state, "partial")
        self.assertEqual(sync.period_id.state, "partial")
        self.assertEqual(sync.detail_count, 1)
        self.assertEqual(sync.error_code, "csv_invalid_rows")
        self.assertIn("Line 3:", sync.error_message)
        self.assertEqual(sync.section_ids.state, "partial")

    def test_no_valid_row_marks_the_read_failed(self):
        sync = self._import(
            self._csv("33,76222222-9,Bad RUT,11,2026-08-01,1000,0,190,0,0,1190")
        )
        self.assertEqual(sync.state, "failed")
        self.assertFalse(sync.document_ids)
        self.assertEqual(sync.section_ids.state, "failed")

    def test_file_errors_store_nothing(self):
        syncs_before = self.env["l10n_cl.dte.rcv.sync"].search_count([])
        with self.assertRaisesRegex(UserError, "missing these columns"):
            self._wizard(b"folio\n1").action_import()
        wizard = self._wizard(b"x")
        wizard.upload = "%%%"
        with self.assertRaisesRegex(UserError, "could not be read"):
            wizard.action_import()
        self.assertEqual(
            self.env["l10n_cl.dte.rcv.sync"].search_count([]), syncs_before
        )

    def test_month_and_company_are_validated(self):
        content = self._csv(
            f"33,{SUPPLIER_RUT},Fake Supplier,10,2026-08-01,1000,0,190,0,0,1190"
        )
        with self.assertRaisesRegex(UserError, "between 1 and 12"):
            self._wizard(content, month=13).action_import()
        other_company = self.env["res.company"].create({"name": "RCV Other Co"})
        wizard = self._wizard(content)
        wizard.company_id = other_company
        with self.assertRaisesRegex(UserError, "companies you are working in"):
            wizard.action_import()

    def test_reader_cannot_import(self):
        with self.assertRaises(AccessError):
            self._wizard(self._csv(), user=self.reader)
