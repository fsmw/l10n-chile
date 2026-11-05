# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).


from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from .common import PosReturnCase


@tagged("post_install", "-at_install", "dte_pos_return")
class TestPosReturnFlow(PosReturnCase):
    def test_standard_tax_included_price_uses_frozen_net_fiscal_unit(self):
        self.tax_19.price_include_override = "tax_included"
        self.product.list_price = 119
        original = self.sale("per_order_invoice")
        order = self.refund(original)
        self.assertEqual(order.amount_total, -119)
        self.assertEqual(order.account_move.amount_tax, 19)

    def test_summary_tax_included_price_preserves_original_fiscal_source(self):
        self.tax_19.price_include_override = "tax_included"
        self.product.list_price = 119
        original = self.sale()
        order = self.refund(original)
        self.assertEqual(order.amount_total, -119)
        self.assertFalse(order.account_move)

    def test_summary_partial_has_direct_credit_and_no_account_move(self):
        original = self.sale()
        order = self.refund(original)
        self.assertFalse(order.account_move)
        self.assertEqual(order.dte_return_operation, "return")
        self.assertEqual(order.dte_document_ids.document_code, "61")
        self.assertEqual(order.dte_document_ids.source_pos_order_id, order)
        self.assertEqual(order.amount_total, -119)

    def test_standard_partial_has_native_posted_accounting_credit(self):
        original = self.sale("per_order_invoice")
        order = self.refund(original)
        self.assertEqual(order.account_move.state, "posted")
        self.assertEqual(order.account_move.move_type, "out_refund")
        self.assertEqual(order.account_move.reversed_entry_id, original.account_move)
        self.assertFalse(order.dte_document_ids)
        self.assertEqual(order.account_move.dte_document_ids.document_code, "61")

    def test_full_untouched_is_cancellation(self):
        original = self.sale()
        order = self.refund(original, 2)
        self.assertEqual(order.dte_return_operation, "cancel")

    def test_remaining_after_partial_is_return(self):
        original = self.sale()
        self.refund(original)
        order = self.refund(original)
        self.assertEqual(order.dte_return_operation, "return")

    def test_over_return_is_blocked(self):
        original = self.sale()
        self.refund(original, 2)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.refund(original)

    def test_uuid_retry_has_one_return_document_and_payment(self):
        original = self.sale()
        data = self.refund_data(original)
        order = self.refund(original, data=data)
        replay = self.refund(original, data=data)
        self.assertEqual(order, replay)
        self.assertEqual(len(order._get_pos_dte_documents()), 1)
        self.assertEqual(len(order.payment_ids), 1)

    def test_preview_is_read_only(self):
        original = self.sale()
        before = self.env["l10n_cl.electronic.artifact"].search_count([])
        preview = original.get_dte_return_preview(
            self.config.id,
            original.id,
            [{"original_line_id": original.lines.id, "quantity": "1"}],
            "Goods return",
        )
        self.assertEqual(preview["accounting_mode"], "session_summary")
        self.assertEqual(
            self.env["l10n_cl.electronic.artifact"].search_count([]), before
        )

    def test_forged_target_is_rejected_before_creating_order(self):
        original = self.sale()
        data = self.refund_data(original)
        data["dte_return_target_document_id"] = original.dte_document_ids.id
        count = self.env["pos.order"].search_count([])
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.refund(original, data=data)
        self.assertEqual(self.env["pos.order"].search_count([]), count)
