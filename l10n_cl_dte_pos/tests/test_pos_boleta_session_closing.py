# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.common import TestPointOfSaleCommon


@tagged("post_install", "-at_install")
class TestPosBoletaSessionClosing(TestPointOfSaleCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company.country_id = cls.env.ref("base.cl")

    def test_new_session_defaults_to_standard_accounting(self):
        session = self.env["pos.session"].create(
            {"config_id": self.pos_config.id, "user_id": self.env.uid}
        )
        self.assertEqual(session.dte_pos_accounting_mode, "per_order_invoice")
        self.assertIn("move_id", session._fields)

    def test_summary_mode_keeps_native_close_move_model(self):
        self.company.sudo().dte_pos_accounting_mode = "session_summary"
        session = self.env["pos.session"].create(
            {"config_id": self.pos_config.id, "user_id": self.env.uid}
        )
        session.action_pos_session_open()
        self.assertEqual(session.dte_pos_accounting_mode, "session_summary")
        self.assertIn("move_id", session._fields)
        self.assertFalse(session.move_id)
