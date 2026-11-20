# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from ..services.rcv_candidates import vat_variants
from ..services.rcv_matcher import RcvMatcher
from .common import CUSTOMER_RUT, SUPPLIER_RUT, RcvMatchingTestCase


@tagged("post_install", "-at_install")
class TestRcvMatching(RcvMatchingTestCase):
    def test_rut_variants_include_dots_and_country_prefix(self):
        variants = vat_variants("CL 76.222.222-1")
        self.assertIn("76222222-1", variants)
        self.assertIn("76.222.222-1", variants)
        self.assertIn("CL762222221", variants)

    def test_purchase_exact_match(self):
        bill = self._bill("934")
        sync = self._read([self._section([self._row(934, SUPPLIER_RUT)])])
        document = self._document(sync, 934)
        self.assertEqual(document.move_id, bill)
        self.assertEqual(document.reconciliation_state, "matched")
        self.assertEqual(document.reconciliation_reason_code, "fiscal_match")
        self.assertEqual(document.tax_comparison_state, "matched")
        self.assertEqual(document.tax_allocation_state, "matched")
        self.assertEqual(document.fiscal_vat_amount, 190)
        self.assertEqual(document.candidate_move_ids, bill)
        self.assertEqual(bill.dte_rcv_document_ids, document)
        self.assertEqual(bill.dte_rcv_state, "matched")
        self.assertEqual(bill.dte_rcv_sync_id, sync)
        log = document.match_log_ids
        self.assertEqual(len(log), 1)
        self.assertEqual(
            (log.event_type, log.move_id, log.reconciliation_reason_code),
            ("automatic", bill, "fiscal_match"),
        )

    def test_vat_allocation_difference_keeps_the_match(self):
        bill = self._bill("935")
        row = self._row(935, SUPPLIER_RUT, vat_amount=0, common_vat=190, total=1190)
        document = self._document(self._read([self._section([row])]), 935)
        self.assertEqual(document.move_id, bill)
        self.assertEqual(document.reconciliation_state, "matched")
        self.assertEqual(
            document.reconciliation_reason_code, "tax_allocation_difference"
        )
        self.assertEqual(document.tax_allocation_state, "different")
        self.assertIn("190", document.tax_allocation_note)

    def test_sale_exact_match_by_document_number(self):
        invoice = self._invoice()
        folio = invoice.l10n_latam_document_number
        sync = self._read(
            [self._section([self._row(int(folio), CUSTOMER_RUT)])], operation="sale"
        )
        document = self._document(sync, int(folio))
        self.assertEqual(document.move_id, invoice)
        self.assertEqual(document.reconciliation_state, "matched")
        self.assertEqual(invoice.dte_rcv_state, "matched")

    def test_sale_prefers_the_issued_dte_and_ignores_rejected_ones(self):
        invoice = self._invoice()
        dte = self.env["l10n_cl.electronic.document"].create(
            {
                "name": "Synthetic DTE",
                "company_id": self.company.id,
                "document_code": "33",
                "source_key": f"account.move,{invoice.id}",
                "source_move_id": invoice.id,
            }
        )
        # The folio and SII outcome are set by the issuance flow; emulate it.
        self.env.cr.execute(
            "UPDATE l10n_cl_electronic_document "
            "SET folio = 4242, fiscal_state = 'accepted' WHERE id = %s",
            [dte.id],
        )
        dte.invalidate_recordset()
        self.assertNotEqual(invoice.l10n_latam_document_number, "4242")
        sync = self._read(
            [self._section([self._row(4242, CUSTOMER_RUT)])], operation="sale"
        )
        document = self._document(sync, 4242)
        self.assertEqual(document.move_id, invoice)
        self.assertEqual(document.reconciliation_state, "matched")
        self.env.cr.execute(
            "UPDATE l10n_cl_electronic_document SET fiscal_state = 'rejected' "
            "WHERE id = %s",
            [dte.id],
        )
        dte.invalidate_recordset()
        RcvMatcher(self.env).reconcile(document)
        self.assertFalse(document.move_id)
        self.assertEqual(document.reconciliation_state, "missing")

    def test_amount_mismatch_is_reported(self):
        bill = self._bill("940")
        row = self._row(940, SUPPLIER_RUT, net=1100, vat_amount=209)
        document = self._document(self._read([self._section([row])]), 940)
        self.assertEqual(document.move_id, bill)
        self.assertEqual(document.reconciliation_state, "amount_diff")
        self.assertEqual(document.reconciliation_reason_code, "fiscal_amount_mismatch")
        self.assertEqual(document.tax_comparison_state, "different")

    def test_period_difference_does_not_change_the_posted_entry(self):
        bill = self._bill("941", date="2026-07-20")
        snapshot = (bill.state, bill.date, bill.line_ids.mapped("balance"))
        document = self._document(
            self._read([self._section([self._row(941, SUPPLIER_RUT)])]), 941
        )
        self.assertEqual(document.move_id, bill)
        self.assertEqual(document.reconciliation_state, "period_diff")
        self.assertEqual(
            document.reconciliation_reason_code, "accounting_period_mismatch"
        )
        self.assertEqual(
            (bill.state, bill.date, bill.line_ids.mapped("balance")), snapshot
        )

    def test_zero_vat_with_residual_needs_source_evidence(self):
        bill = self._bill("942")
        row = self._row(942, SUPPLIER_RUT, vat_amount=0, total=1190)
        document = self._document(self._read([self._section([row])]), 942)
        self.assertEqual(document.move_id, bill)
        self.assertEqual(document.reconciliation_state, "review")
        self.assertEqual(
            document.reconciliation_reason_code, "unexplained_tax_residual"
        )
        self.assertIn("190", document.reconciliation_message)

    def test_incomplete_tax_breakdown_is_not_comparable(self):
        bill = self._bill("943")
        row = self._row(943, SUPPLIER_RUT)
        row["detMntIVANoRec"] = None
        document = self._document(self._read([self._section([row])]), 943)
        self.assertFalse(document.tax_breakdown_available)
        self.assertEqual(document.move_id, bill)
        self.assertEqual(document.reconciliation_state, "review")
        self.assertEqual(document.reconciliation_reason_code, "tax_mapping_unavailable")

    def test_duplicate_candidates_are_not_linked(self):
        first = self._bill("950", post=False)
        second = self._bill("950", post=False)
        document = self._document(
            self._read([self._section([self._row(950, SUPPLIER_RUT)])]), 950
        )
        self.assertFalse(document.move_id)
        self.assertEqual(document.candidate_move_ids, first | second)
        self.assertEqual(document.reconciliation_state, "duplicate")
        self.assertEqual(
            document.reconciliation_reason_code, "duplicate_fiscal_identity"
        )

    def test_cancelled_and_other_supplier_entries_are_not_candidates(self):
        cancelled = self._bill("951", post=False)
        cancelled.button_cancel()
        other = self._cl_partner("Other Fake Supplier", "77333333-5")
        self._bill("951", partner=other)
        document = self._document(
            self._read([self._section([self._row(951, SUPPLIER_RUT)])]), 951
        )
        self.assertFalse(document.candidate_move_ids)
        self.assertEqual(document.reconciliation_state, "missing")
        self.assertEqual(document.reconciliation_reason_code, "odoo_document_missing")

    def test_unmatched_document_of_an_incomplete_section_is_not_concluded(self):
        section = self._section([self._row(960, SUPPLIER_RUT)], complete=False)
        sync = self._read([section])
        self.assertEqual(sync.section_ids.state, "partial")
        document = self._document(sync, 960)
        self.assertEqual(document.reconciliation_state, "indeterminate")
        self.assertEqual(document.reconciliation_reason_code, "incomplete_rcv_section")

    def test_entry_absence_requires_every_purchase_state_complete(self):
        bill = self._bill("970")
        complete = [
            self._section([], state=state) for state in ("REGISTRO", "RECLAMADO")
        ]
        self._read([*complete, self._section([], state="PENDIENTE")])
        self.assertEqual(bill.dte_rcv_state, "not_in_sii")
        pending = self._section([self._row(999, SUPPLIER_RUT)], "PENDIENTE", False)
        sync = self._read([*complete, pending])
        self.assertEqual(bill.dte_rcv_state, "indeterminate")
        self.assertEqual(bill.dte_rcv_sync_id, sync)

    def test_automatic_log_is_idempotent(self):
        self._bill("980")
        sync = self._read([self._section([self._row(980, SUPPLIER_RUT)])])
        document = self._document(sync, 980)
        matcher = RcvMatcher(self.env)
        matcher.reconcile(document)
        matcher.reconcile(document)
        self.assertEqual(len(document.match_log_ids), 1)
        document.move_id.button_draft()
        document.move_id.button_cancel()
        matcher.reconcile(document)
        self.assertEqual(document.reconciliation_state, "missing")
        self.assertEqual(len(document.match_log_ids), 2)

    def test_generic_export_with_invalid_identity_goes_to_review(self):
        row = self._row("A1", "55555555-5", code=110, vat_amount=0)
        document = self._document(
            self._read([self._section([row])], operation="sale"), "A1"
        )
        self.assertEqual(document.reconciliation_state, "review")
        self.assertEqual(document.reconciliation_reason_code, "invalid_fiscal_identity")

    def test_period_report_renders_reconciliation(self):
        self._bill("990")
        sync = self._read([self._section([self._row(990, SUPPLIER_RUT)])])
        sync.period_id.current_sync_id = sync
        html, _report_format = self.env["ir.actions.report"]._render_qweb_html(
            "l10n_cl_dte_rcv.action_report_rcv_period", sync.period_id.ids
        )
        self.assertIn(b"990", html)
        self.assertIn(b"Matched", html)
