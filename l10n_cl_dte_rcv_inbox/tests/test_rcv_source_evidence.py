# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json

from odoo.tests import tagged

from .common import HEADER_SUPPLIER_RUT, RcvInboxCase


@tagged("post_install", "-at_install")
class TestRcvSourceEvidence(RcvInboxCase):
    def zero_vat_document(self, source, total=1190):
        # The RCV reports zero VAT and leaves the VAT as an unexplained residual.
        return self.rcv_document(source.folio, vat_amount=0, total=total)

    def test_signed_xml_confirms_the_vat_of_a_zero_vat_purchase(self):
        source = self.received()
        bill = self.bound_bill(source)
        document = self.zero_vat_document(source)
        self.assertEqual(document.move_id, bill)
        self.assertEqual(document.tax_comparison_state, "source_confirmed")
        self.assertEqual(document.reconciliation_state, "matched")
        self.assertEqual(
            document.reconciliation_reason_code, "rcv_source_inconsistency_resolved"
        )
        evidence = json.loads(document.match_log_ids[:1].decision_data)["evidence"]
        source_evidence = evidence["source_evidence"]
        self.assertTrue(source_evidence["verified"])
        self.assertEqual(source_evidence["reason_code"], "signed_source_vat")
        self.assertEqual(source_evidence["incoming_dte_id"], source.id)

    def test_signed_vat_must_explain_the_residual(self):
        source = self.received()
        self.bound_bill(source)
        document = self.zero_vat_document(source, total=1200)
        self.assertEqual(document.reconciliation_state, "review")
        self.assertEqual(
            document.reconciliation_reason_code, "unexplained_tax_residual"
        )
        evidence = document._get_rcv_source_tax_evidence(document.move_id, 200)
        self.assertFalse(evidence["verified"])
        self.assertEqual(evidence["reason_code"], "source_vat_mismatch")

    def test_entry_must_book_the_signed_vat(self):
        source = self.received()
        bill = self.bound_bill(source)
        document = self.zero_vat_document(source)
        bill.sudo().invoice_line_ids.tax_ids = self.purchase_exempt
        evidence = document._get_rcv_source_tax_evidence(bill, 190)
        self.assertFalse(evidence["verified"])
        self.assertEqual(evidence["reason_code"], "booked_vat_mismatch")

    def test_entry_without_received_dte_is_not_verified(self):
        document = self.rcv_document(701, HEADER_SUPPLIER_RUT, vat_amount=0)
        move = self.rcv_env["account.move"]
        evidence = document._get_rcv_source_tax_evidence(move, 190)
        self.assertFalse(evidence["verified"])
        self.assertEqual(evidence["reason_code"], "missing_source_document")
