# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from odoo.addons.l10n_cl_dte_account.services.dte_issuance_service import (
    DteIssuanceService,
)
from odoo.addons.l10n_cl_dte_note.services.note_reference_service import (
    NoteReferenceService,
)

from ..services.pos_line_mapping import PosLineMappingService
from ..services.pos_return_policy import PosReturnPolicyService
from .common import PosReturnCase


@tagged("post_install", "-at_install", "dte_pos_return")
class TestPosReturnAcceptance(PosReturnCase):
    def test_summary_refund_has_translation_context(self):
        with self.assertNoLogs("odoo.tools.translate", level="WARNING"):
            original = self.sale(quantity=1)
            order = self.refund(original)
        self.assertEqual(order.dte_return_operation, "cancel")

    def test_standard_refund_has_translation_context(self):
        with self.assertNoLogs("odoo.tools.translate", level="WARNING"):
            original = self.sale("per_order_invoice")
            order = self.refund(original)
        self.assertEqual(order.dte_return_operation, "return")

    def test_foreign_pos_user_does_not_need_dte_group(self):
        self.company.country_id = self.env.ref("base.us")
        self.open_new_session()
        user = new_test_user(
            self.env,
            login="foreign_pos_cashier",
            groups="base.group_user,point_of_sale.group_pos_user",
            company_id=self.company.id,
        )
        data = self.create_ui_order_data([(self.product, 1)], customer=self.customer)
        response = self.env["pos.order"].with_user(user).sync_from_ui([data])
        order = self.env["pos.order"].browse(response["pos.order"][0]["id"])
        self.assertEqual(order.state, "paid")
        self.assertFalse(order._get_pos_dte_documents())

    def legacy_sale(self, duplicate=False):
        self.company.dte_pos_returns_enabled = False
        if duplicate:
            self.company.dte_pos_accounting_mode = "session_summary"
            self.open_new_session()
            data = self.create_ui_order_data(
                [(self.product, 1), (self.product, 1)], customer=self.customer
            )
            response = self.env["pos.order"].sync_from_ui([data])
            original = self.env["pos.order"].browse(response["pos.order"][0]["id"])
        else:
            original = self.sale()
        self.close(original.session_id)
        self.company.dte_pos_returns_enabled = True
        self.open_new_session()
        return original

    def test_legacy_sale_requires_mapping_before_payment(self):
        original = self.legacy_sale()
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.refund(original)

    def test_legacy_unique_mapping_preserves_source_digest(self):
        original = self.legacy_sale()
        digest = original.dte_document_ids.payload_digest
        original.action_prepare_dte_line_mapping()
        order = self.refund(original)
        self.assertEqual(order.dte_document_ids.document_code, "61")
        self.assertEqual(original.dte_document_ids.payload_digest, digest)

    def test_legacy_duplicate_details_are_never_guessed(self):
        original = self.legacy_sale(duplicate=True)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            original.action_prepare_dte_line_mapping()
        self.assertFalse(original.dte_document_ids.pos_line_mapping_artifact_id)

    def test_fiscal_order_cannot_gain_line_by_direct_rpc(self):
        original = self.sale()
        with self.assertRaises(UserError), self.cr.savepoint():
            self.env["pos.order.line"].create(
                {
                    "order_id": original.id,
                    "product_id": self.product.id,
                    "qty": 1,
                    "price_unit": 100,
                }
            )

    def test_forged_subtotal_is_blocked(self):
        original = self.sale()
        self.assert_payload_blocked(
            original, lambda data: data["lines"][0][2].update(price_subtotal=-1)
        )

    def test_forged_order_total_and_payment_are_blocked(self):
        original = self.sale()

        def forge(data):
            data["amount_total"] = -1
            data["amount_tax"] = 0
            data["payment_ids"][0][2]["amount"] = -1

        self.assert_payload_blocked(original, forge)

    def test_fractional_clp_payment_is_blocked(self):
        original = self.sale()
        self.assert_payload_blocked(
            original, lambda data: data["payment_ids"][0][2].update(amount=-119.1)
        )

    def test_session_policy_cannot_be_forged(self):
        original = self.sale()
        with self.assertRaises(UserError), self.cr.savepoint():
            original.session_id.write({"dte_pos_returns_enabled": False})

    def assert_payload_blocked(self, original, change, exception=ValidationError):
        data = self.refund_data(original)
        change(data)
        before = self.env["pos.order"].search_count([])
        with self.assertRaises(exception), self.cr.savepoint():
            self.refund(original, data=data)
        self.assertEqual(before, self.env["pos.order"].search_count([]))

    def test_cross_store_same_company(self):
        original = self.sale()
        self.other_store()
        order = self.refund(original)
        self.assertNotEqual(order.config_id, original.config_id)
        self.assertEqual(order.company_id, original.company_id)
        self.assertEqual(order.picking_ids.picking_type_id, self.config.picking_type_id)

    def test_standard_origin_in_summary_session(self):
        original = self.sale("per_order_invoice")
        self.close(original.session_id)
        self.company.dte_pos_accounting_mode = "session_summary"
        self.open_new_session()
        order = self.refund(original)
        self.assertEqual(order.session_id.dte_pos_accounting_mode, "session_summary")
        self.assertEqual(order.account_move.move_type, "out_refund")
        self.close(order.session_id)

    def test_summary_origin_in_standard_session(self):
        original = self.sale()
        self.close(original.session_id)
        self.company.dte_pos_accounting_mode = "per_order_invoice"
        self.open_new_session()
        order = self.refund(original)
        self.assertEqual(order.session_id.dte_pos_accounting_mode, "per_order_invoice")
        self.assertFalse(order.account_move)
        self.close(order.session_id)

    def test_native_summary_closing_includes_refund_once(self):
        original = self.sale()
        order = self.refund(original)
        self.assertEqual(order.session_id.dte_pos_total, 119)
        self.assertEqual(order.session_id.dte_return_total, -119)
        self.close(order.session_id)
        self.assertFalse(order.account_move)
        self.assertAlmostEqual(
            sum(order.session_id.move_id.line_ids.mapped("balance")), 0
        )

    def test_native_standard_closing_does_not_duplicate_credit(self):
        original = self.sale("per_order_invoice")
        order = self.refund(original)
        credit = order.account_move
        self.close(order.session_id)
        self.assertEqual(order.account_move, credit)
        self.assertTrue(credit.payment_state in {"paid", "in_payment", "reversed"})

    def test_mixed_sale_and_return_is_blocked(self):
        original = self.sale()
        self.assert_payload_blocked(
            original,
            lambda data: data["lines"].append(
                self.create_ui_order_data([(self.product, 1)], customer=self.customer)[
                    "lines"
                ][0]
            ),
        )

    def test_original_price_cannot_be_changed(self):
        original = self.sale()
        self.assert_payload_blocked(
            original, lambda data: data["lines"][0][2].update(price_unit=90)
        )

    def test_original_discount_cannot_be_changed(self):
        original = self.sale()
        self.assert_payload_blocked(
            original, lambda data: data["lines"][0][2].update(discount=10)
        )

    def test_reason_is_required(self):
        original = self.sale()
        self.assert_payload_blocked(
            original, lambda data: data.update(dte_return_reason="")
        )

    def test_reason_is_limited_to_90_characters(self):
        original = self.sale()
        self.assert_payload_blocked(
            original, lambda data: data.update(dte_return_reason="a" * 91)
        )

    def test_reason_must_be_encodable(self):
        original = self.sale()
        self.assert_payload_blocked(
            original, lambda data: data.update(dte_return_reason="Return 😀")
        )

    def test_draft_refund_is_blocked(self):
        original = self.sale()
        self.assert_payload_blocked(
            original, lambda data: data.update(state="draft"), UserError
        )

    def test_forged_line_mapping_is_blocked(self):
        original = self.sale()
        self.assert_payload_blocked(
            original,
            lambda data: data["lines"][0][2].update(dte_return_origin_line_number=1),
            AccessError,
        )

    def test_forged_context_cannot_assign_source(self):
        original = self.sale()
        with self.assertRaises(AccessError), self.cr.savepoint():
            original.with_context(_dte_pos_native_binding=True).write(
                {"dte_return_target_document_id": original.dte_document_ids.id}
            )

    def test_mapping_is_immutable_and_digest_checked(self):
        original = self.sale()
        mapping = original.dte_document_ids.pos_line_mapping_artifact_id
        digest = mapping.sha256
        with self.assertRaises(AccessError), self.cr.savepoint():
            mapping.write({"content": base64.b64encode(b"tampered")})
        self.refund(original)
        self.assertEqual(mapping.sha256, digest)
        self.assertEqual(
            PosLineMappingService(self.env).read_mapping(original.dte_document_ids)[
                "pos_order_id"
            ],
            original.id,
        )

    def test_issued_order_economics_cannot_be_changed(self):
        original = self.sale()
        with self.assertRaises(UserError), self.cr.savepoint():
            original.lines.write({"qty": 3})

    def test_issued_order_cannot_be_deleted(self):
        original = self.sale()
        with self.assertRaises(UserError), self.cr.savepoint():
            original.unlink()

    def test_issued_payments_cannot_be_changed(self):
        original = self.sale()
        with self.assertRaises(UserError), self.cr.savepoint():
            original.payment_ids.write({"amount": 100})

    def test_issued_order_cannot_gain_payment(self):
        original = self.sale()
        with self.assertRaises(UserError), self.cr.savepoint():
            self.env["pos.payment"].create(
                {
                    "pos_order_id": original.id,
                    "payment_method_id": original.payment_ids.payment_method_id.id,
                    "amount": 10,
                }
            )

    def test_source_snapshot_is_unchanged_by_return(self):
        original = self.sale()
        snapshots = original.dte_document_ids.artifact_ids.filtered(
            lambda artifact: artifact.role == "source_snapshot"
        )
        content, digest = snapshots.content, snapshots.sha256
        self.refund(original)
        self.assertEqual(snapshots.content, content)
        self.assertEqual(snapshots.sha256, digest)

    def test_terminal_refund_is_blocked_before_payment(self):
        original = self.sale()
        self.close(original.session_id)
        with patch.object(
            type(original.payment_ids.payment_method_id),
            "_get_payment_terminal_selection",
            return_value=[("test", "Test")],
        ):
            original.payment_ids.payment_method_id.use_payment_terminal = "test"
            self.open_new_session()
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self.refund(original)

    def test_changed_original_tax_is_blocked(self):
        original = self.sale()
        self.close(original.session_id)
        self.tax_19.amount = 0
        self.open_new_session()
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.refund(original)

    def test_summary_exempt_boleta_returns_nc61(self):
        self.product.taxes_id = [Command.clear()]
        original = self.sale()
        order = self.refund(original)
        self.assertEqual(original.dte_document_ids.document_code, "41")
        snapshot = NoteReferenceService(self.env).read_original_snapshot(
            order.dte_document_ids
        )
        self.assertEqual(snapshot["totals"]["exempt"], "100")
        self.assertFalse(snapshot["totals"]["vat"])

    def test_full_cancel_has_summary_and_original_effects(self):
        order = self.refund(self.sale(), 2)
        snapshot = NoteReferenceService(self.env).read_original_snapshot(
            order.dte_document_ids
        )
        self.assertEqual(snapshot["reference"]["reference_code"], "1")
        self.assertEqual(
            snapshot["adjustment"]["serialization_mode"], "full_cancel_summary"
        )
        self.assertEqual(
            snapshot["adjustment"]["line_effects"][0]["quantity_delta"], "2"
        )

    def test_partial_has_codref3_and_original_reference(self):
        original = self.sale()
        order = self.refund(original)
        snapshot = NoteReferenceService(self.env).read_original_snapshot(
            order.dte_document_ids
        )
        self.assertEqual(snapshot["reference"]["reference_code"], "3")
        self.assertEqual(
            snapshot["reference"]["folio"], original.dte_document_ids.folio
        )
        self.assertEqual(snapshot["reference"]["document_code"], "39")

    def test_rejected_note_keeps_physical_capacity_consumed(self):
        original = self.sale()
        order = self.refund(original, 2)
        self.fiscal_response(order.dte_document_ids, "rejected")
        self.assertEqual(
            NoteReferenceService(self.env).calculate_available_amounts(
                original.dte_document_ids
            )["total"],
            238,
        )
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.refund(original)

    def test_rejected_note_blocks_both_close_entrypoints(self):
        order = self.refund(self.sale())
        self.fiscal_response(order.dte_document_ids, "rejected")
        for action in (
            order.session_id.action_pos_session_close,
            order.session_id.action_pos_session_closing_control,
        ):
            with self.assertRaises(UserError), self.cr.savepoint():
                action()

    def test_receipt_recovers_without_another_payment(self):
        order = self.refund(self.sale())
        before = len(order.payment_ids)
        receipt = order.get_dte_return_receipt(order.config_id.id, order.uuid)
        self.assertEqual(receipt["folio"], order.dte_document_ids.folio)
        self.assertNotEqual(receipt["status"], "accepted")
        self.assertEqual(len(order.payment_ids), before)

    def test_search_uses_native_tuple_protocol(self):
        original = self.sale()
        self.other_store()
        response = original.search_dte_return_order_ids(self.config.id, "")
        self.assertIn(original.id, [item[0] for item in response["ordersInfo"]])

    def test_policy_cannot_change_during_open_session(self):
        self.sale()
        with self.assertRaises(UserError), self.cr.savepoint():
            self.company.dte_pos_returns_enabled = False

    def test_plain_pos_user_cannot_issue_credit(self):
        original = self.sale()
        user = new_test_user(
            self.env,
            login="return_without_dte",
            groups="base.group_user,point_of_sale.group_pos_user",
            company_id=self.company.id,
        )
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.env["pos.order"].with_user(user).sync_from_ui(
                [self.refund_data(original)]
            )

    def test_pos_dte_user_can_issue_summary_credit(self):
        original = self.sale()
        user = new_test_user(
            self.env,
            login="return_with_dte",
            groups=(
                "base.group_user,point_of_sale.group_pos_user,l10n_cl_dte"
                "_core.group_dte_user"
            ),
            company_id=self.company.id,
        )
        response = (
            self.env["pos.order"]
            .with_user(user)
            .sync_from_ui([self.refund_data(original)])
        )
        order = self.env["pos.order"].browse(response["pos.order"][0]["id"])
        self.assertEqual(order.dte_document_ids.document_code, "61")

    def test_ordinary_note_lock_stays_manager_only(self):
        original = self.sale()
        user = new_test_user(
            self.env,
            login="return_not_manager",
            groups=(
                "base.group_user,point_of_sale.group_pos_user,l10n_cl_dte"
                "_core.group_dte_user"
            ),
            company_id=self.company.id,
        )
        with self.assertRaises(AccessError), self.cr.savepoint():
            original.dte_document_ids.with_user(user)._lock_note_chain(
                original.dte_document_ids.with_user(user)
            )

    def test_issuance_failure_rolls_back_native_effects(self):
        original = self.sale("per_order_invoice")
        counts = {
            name: self.env[name].search_count([])
            for name in ("pos.order", "pos.payment", "stock.picking", "account.move")
        }
        with (
            patch.object(
                DteIssuanceService,
                "issue",
                side_effect=ValidationError("Synthetic signing failure"),
            ),
            self.assertRaises(ValidationError),
        ):
            self.refund(original)
        for name, count in counts.items():
            self.assertEqual(self.env[name].search_count([]), count, name)

    def test_rounding_residual_is_blocked_without_adjustment_product(self):
        self.product.list_price = 0.5
        original = self.sale()
        self.refund(original)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.refund(original)

    def test_cross_company_preview_is_denied_even_if_allowed(self):
        original = self.sale()
        other = self.env["res.company"].create(
            {
                "name": "Other Return Company",
                "country_id": self.env.ref("base.cl").id,
                "currency_id": self.env.ref("base.CLP").id,
                "dte_pos_returns_enabled": True,
            }
        )
        self.env.user.company_ids += other
        session = original.session_id
        with (
            patch.object(type(session), "company_id", other),
            self.assertRaises(AccessError),
        ):
            PosReturnPolicyService(self.env).original(original, session)

    def test_manual_note_and_pos_return_share_fiscal_capacity(self):
        original = self.sale("per_order_invoice")
        wizard = self.env["account.move.reversal"].create(
            {
                "move_ids": [Command.set(original.account_move.ids)],
                "journal_id": original.account_move.journal_id.id,
                "date": original.account_move.invoice_date,
                "reason": "Synthetic full credit",
                "dte_note_operation": "cancel",
            }
        )
        wizard.reverse_moves()
        credit = wizard.new_move_ids
        target = original.account_move.dte_document_ids
        credit.write(
            {
                "dte_note_target_document_id": target.id,
                "dte_note_operation": "cancel",
                "dte_note_reason": "Synthetic full credit",
                "l10n_latam_document_type_id": self.env["l10n_latam.document.type"]
                .search(
                    [
                        ("country_id", "=", self.env.ref("base.cl").id),
                        ("code", "=", "61"),
                    ],
                    limit=1,
                )
                .id,
            }
        )
        credit.action_post()
        DteIssuanceService().issue(credit, credit._get_dte_issuance_adapter("61"), "61")
        self.assertFalse(original.lines.refund_orderline_ids)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.refund(original)
