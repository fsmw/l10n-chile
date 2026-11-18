# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.common import TestPointOfSaleCommon


@tagged("post_install", "-at_install")
class TestPosBoletaAccountingModes(TestPointOfSaleCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company.country_id = cls.env.ref("base.cl")

    def test_session_captures_company_policy_on_open(self):
        self.company.sudo().dte_pos_accounting_mode = "session_summary"
        session = self.env["pos.session"].create(
            {"config_id": self.pos_config.id, "user_id": self.env.uid}
        )
        self.assertEqual(session.dte_pos_accounting_mode, "session_summary")
        session.action_pos_session_open()
        self.assertEqual(session.dte_pos_accounting_mode, "session_summary")

    def test_session_mode_has_safe_default(self):
        # Odoo uses this default to initialize sessions when adding the column.
        defaults = self.env["pos.session"].default_get(["dte_pos_accounting_mode"])
        self.assertEqual(defaults["dte_pos_accounting_mode"], "per_order_invoice")
        session = self.env["pos.session"].create(
            {"config_id": self.pos_config.id, "user_id": self.env.uid}
        )
        self.assertEqual(session.dte_pos_accounting_mode, "per_order_invoice")

    def test_company_policy_cannot_change_with_active_session(self):
        self.env["pos.session"].create(
            {"config_id": self.pos_config.id, "user_id": self.env.uid}
        )
        with self.assertRaisesRegex(UserError, "Close all active POS sessions"):
            self.company.sudo().dte_pos_accounting_mode = "session_summary"
