# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from types import SimpleNamespace
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase

from odoo.addons.l10n_cl_dte_account.services.dte_issuance_adapter import (
    DteIssuanceAdapter,
)
from odoo.addons.l10n_cl_dte_account.services.dte_issuance_service import (
    DteIssuanceService,
)
from odoo.addons.l10n_cl_dte_pos.services.pos_boleta_issuance_adapter import (
    PosBoletaIssuanceAdapter,
)
from odoo.addons.l10n_cl_dte_pos.services.pos_boleta_snapshot import (
    PosBoletaSnapshotService,
)
from odoo.addons.point_of_sale.tests.common import TestPoSCommon


class FakePayments(list):
    def filtered(self, predicate):
        return FakePayments(payment for payment in self if predicate(payment))

    def mapped(self, dotted_path):
        values = []
        for item in self:
            value = item
            for field_name in dotted_path.split("."):
                value = getattr(value, field_name)
            values.append(value)
        return values


class TestPosBoletaDte(TransactionCase):
    def test_invoice_source_key_remains_backward_compatible(self):
        adapter = DteIssuanceAdapter()
        source = SimpleNamespace(_name="account.move", id=42)
        self.assertEqual(adapter.source_key(source, "39"), "account.move:42:type-39")

    def test_session_summary_has_dedicated_pos_source_identity(self):
        adapter = PosBoletaIssuanceAdapter()
        source = SimpleNamespace(_name="pos.order", id=42)
        self.assertEqual(adapter.source_key(source, "41"), "pos.order:42:type-41")
        self.assertEqual(
            adapter.document_source_values(source), {"source_pos_order_id": 42}
        )

    def test_unsupported_type_fails_before_caf_or_submission(self):
        service = PosBoletaSnapshotService()
        with self.assertRaisesRegex(ValidationError, "Only Chilean boletas"):
            service.validate_source(
                SimpleNamespace(_name="pos.order", ensure_one=lambda: True), "33"
            )

    def test_high_value_payments_need_one_explicit_sii_code(self):
        service = PosBoletaSnapshotService()
        payments = FakePayments(
            [
                SimpleNamespace(
                    amount=100,
                    is_change=False,
                    payment_method_id=SimpleNamespace(
                        l10n_cl_dte_boleta_payment_method="2"
                    ),
                ),
                SimpleNamespace(
                    amount=200,
                    is_change=False,
                    payment_method_id=SimpleNamespace(
                        l10n_cl_dte_boleta_payment_method="2"
                    ),
                ),
            ]
        )
        order = SimpleNamespace(payment_ids=payments)
        self.assertEqual(service._payment_method_for_order(order, required=True), "2")
        payments[1].payment_method_id.l10n_cl_dte_boleta_payment_method = "3"
        with self.assertRaisesRegex(ValidationError, "must map to one SII code"):
            service._payment_method_for_order(order, required=True)
        payments[1].payment_method_id.l10n_cl_dte_boleta_payment_method = False
        with self.assertRaisesRegex(ValidationError, "Map every POS payment method"):
            service._payment_method_for_order(order, required=True)


class TestPosBoletaOrderFlow(TestPoSCommon):
    def setUp(self):
        super().setUp()
        self.company.country_id = self.env.ref("base.cl")
        self.env.user.sudo().write(
            {
                "groups_id": [
                    Command.link(self.env.ref("l10n_cl_dte_core.group_dte_user").id)
                ]
            }
        )
        self.config = self.basic_config
        self.tax_19 = self.taxes["tax7"]
        self.tax_19.write({"amount": 19, "name": "POS Chile VAT 19%"})
        self.boleta_product = self.create_product(
            "POS DTE Product",
            self.categ_basic,
            100,
            tax_ids=[self.tax_19.id],
        )

    def _paid_order_data(self):
        self.open_new_session()
        return self.create_ui_order_data(
            [(self.boleta_product, 1)],
            customer=self.customer,
        )

    def test_standard_mode_forces_invoice_and_uses_account_move_source(self):
        order_data = self._paid_order_data()
        with patch.object(DteIssuanceService, "issue", autospec=True) as issue:
            result = self.env["pos.order"].sync_from_ui([order_data])
        order = self.env["pos.order"].browse(result["pos.order"][0]["id"])
        self.assertTrue(order.account_move)
        self.assertEqual(order.account_move.state, "posted")
        self.assertEqual(order.account_move.l10n_latam_document_type_id.code, "39")
        self.assertEqual(issue.call_args.args[1], order.account_move)
        self.assertEqual(issue.call_args.args[3], "39")

    def test_summary_mode_skips_invoice_and_uses_pos_order_source(self):
        self.company.sudo().dte_pos_accounting_mode = "session_summary"
        order_data = self._paid_order_data()
        with patch.object(DteIssuanceService, "issue", autospec=True) as issue:
            result = self.env["pos.order"].sync_from_ui([order_data])
        order = self.env["pos.order"].browse(result["pos.order"][0]["id"])
        self.assertEqual(order.state, "paid")
        self.assertFalse(order.account_move)
        self.assertEqual(issue.call_args.args[1], order)
        self.assertEqual(
            issue.call_args.args[2].source_key(order, "39"),
            f"pos.order:{order.id}:type-39",
        )
