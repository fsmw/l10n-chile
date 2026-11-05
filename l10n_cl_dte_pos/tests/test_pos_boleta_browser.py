# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from decimal import Decimal
from unittest.mock import patch

from odoo import Command
from odoo.tests import tagged
from odoo.tests.common import mute_logger

from odoo.addons.l10n_cl_dte_boleta.services.boleta_snapshot import (
    BoletaSnapshotService,
)
from odoo.addons.l10n_cl_dte_signing.tests.common import SigningTestCase
from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon


@tagged("post_install", "-at_install")
class TestPosBoletaBrowser(TestPointOfSaleHttpCommon, SigningTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.groups_id += cls.env.ref("l10n_cl_dte_core.group_dte_manager")
        cls.company = cls.company_data["company"]
        for user in (cls.dte_manager, cls.dte_user, cls.pos_user, cls.pos_admin):
            user.sudo().write(
                {
                    "company_id": cls.company.id,
                    "company_ids": [Command.link(cls.company.id)],
                }
            )
        cls.company.write(
            {
                "country_id": cls.env.ref("base.cl").id,
                "currency_id": cls.env.ref("base.CLP").id,
                "dte_sii_environment": "demo",
                "dte_sii_activity_code": "620200",
                "l10n_cl_activity_description": "Servicios de pruebas",
            }
        )
        cls.company.partner_id.write(
            {
                "name": "Synthetic POS Issuer",
                "vat": "76864280-K",
                "street": "Calle Prueba 100",
                "city": "Santiago",
            }
        )
        cls.pos_user.write(
            {
                "groups_id": [
                    Command.link(cls.env.ref("l10n_cl_dte_core.group_dte_user").id)
                ],
            }
        )
        cls.partner_test_1.write(
            {
                "name": "Synthetic POS Receiver",
                "vat": "76864280-K",
                "country_id": cls.env.ref("base.cl").id,
            }
        )
        cls.test_tax = cls.env["account.tax"].create(
            {"name": "POS Test VAT 19%", "amount": 19, "type_tax_use": "sale"}
        )
        cls.whiteboard_pen.write(
            {
                "name": "DTE Browser Product",
                "list_price": 100,
                "taxes_id": [Command.set(cls.test_tax.ids)],
            }
        )
        cls.main_pos_config.pricelist_id.currency_id = cls.env.ref("base.CLP")
        cls.main_pos_config.pricelist_id.item_ids.unlink()
        cls.main_pos_config.write(
            {
                "cash_control": True,
                "payment_method_ids": [Command.link(cls.bank_payment_method.id)],
                "available_pricelist_ids": [
                    Command.set(cls.main_pos_config.pricelist_id.ids)
                ],
            }
        )
        cls.main_pos_config.payment_method_ids.write(
            {"l10n_cl_dte_boleta_payment_method": "2"}
        )

    def _run_accounting_mode(self, mode, refund=False):
        self.company.dte_pos_accounting_mode = mode
        self.validate_authorization(document_code="39", folio_start=910, folio_end=930)
        self.create_signing_profile()
        self.main_pos_config.with_user(self.pos_user).open_ui()
        session = self.main_pos_config.current_session_id
        with patch.object(
            BoletaSnapshotService,
            "_annual_135_uf_limit_for",
            return_value=Decimal("999999999"),
        ):
            self.start_pos_tour("l10n_cl_dte_pos_sales", login="pos_user")
        orders = session.order_ids
        self.assertEqual(len(orders), 2)
        positive_payments = orders.payment_ids.filtered(
            lambda payment: payment.amount > 0
        )
        self.assertEqual(
            len(positive_payments),
            3,
            [
                (payment.amount, payment.payment_method_id.name, payment.is_change)
                for payment in orders.payment_ids
            ],
        )
        for order in orders:
            self.assertEqual(
                sum(order.payment_ids.mapped("amount")), order.amount_total
            )
        self.assertEqual(session.dte_order_count, 2)
        self.assertEqual(session.dte_pending_order_count, 0)
        documents = orders.mapped("dte_document_ids") | orders.mapped(
            "account_move.dte_document_ids"
        )
        self.assertEqual(len(documents), 2)
        self.assertEqual(set(documents.mapped("preparation_state")), {"queued"})
        if mode == "per_order_invoice":
            self.assertEqual(len(orders.account_move), 2)
            self.assertEqual(set(orders.account_move.mapped("state")), {"posted"})
            self.assertFalse(documents.source_pos_order_id)
        else:
            self.assertFalse(orders.account_move)
            self.assertEqual(set(documents.source_pos_order_id.ids), set(orders.ids))
        if refund:
            # A handled UserError is the expected browser outcome for refunds.
            with mute_logger("odoo.http"):
                self.start_pos_tour("l10n_cl_dte_pos_refund_blocked", login="pos_user")
            self.assertEqual(session.order_ids, orders)
            self.assertEqual(
                set(
                    (orders.dte_document_ids | orders.account_move.dte_document_ids).ids
                ),
                set(documents.ids),
            )
            return
        self.start_pos_tour("l10n_cl_dte_pos_close", login="pos_user")
        self.assertEqual(session.state, "closed")
        self.assertEqual(session.move_id.state, "posted")
        self.assertAlmostEqual(sum(session.move_id.line_ids.mapped("balance")), 0)

    def test_browser_standard_sales_and_session_close(self):
        self._run_accounting_mode("per_order_invoice")

    def test_browser_summary_sales_and_session_close(self):
        self._run_accounting_mode("session_summary")

    def test_browser_standard_refund_is_blocked_without_new_dte(self):
        self._run_accounting_mode("per_order_invoice", refund=True)

    def test_browser_summary_refund_is_blocked_without_new_dte(self):
        self._run_accounting_mode("session_summary", refund=True)

    def test_browser_non_chilean_payment_keeps_invoice_toggle(self):
        self.company.country_id = self.env.ref("base.us")
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour("l10n_cl_dte_pos_foreign_policy", login="pos_user")
