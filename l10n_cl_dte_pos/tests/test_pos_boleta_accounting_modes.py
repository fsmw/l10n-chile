# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _
from odoo.exceptions import UserError

from odoo.addons.point_of_sale.tests.common import TestPointOfSaleCommon


class TestPosBoletaAccountingModes(TestPointOfSaleCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company.country_id = cls.env.ref("base.cl")

    def test_company_default_is_per_order_invoice(self):
        company = self.env["res.company"].create({"name": _("POS DTE Default")})
        self.assertEqual(company.sudo().dte_pos_accounting_mode, "per_order_invoice")

    def test_session_captures_company_policy_on_open(self):
        self.company.sudo().dte_pos_accounting_mode = "session_summary"
        session = self.env["pos.session"].create(
            {"config_id": self.pos_config.id, "user_id": self.env.uid}
        )
        session.action_pos_session_open()
        self.assertEqual(session.dte_pos_accounting_mode, "session_summary")

    def test_company_policy_cannot_change_with_active_session(self):
        self.env["pos.session"].create(
            {"config_id": self.pos_config.id, "user_id": self.env.uid}
        )
        with self.assertRaisesRegex(UserError, "Close all active POS sessions"):
            self.company.sudo().dte_pos_accounting_mode = "session_summary"

    def test_policy_is_company_wide_not_pos_config(self):
        self.assertNotIn("dte_pos_accounting_mode", self.env["pos.config"]._fields)
        config = self.pos_config.copy({"name": "Second POS"})
        self.company.sudo().dte_pos_accounting_mode = "session_summary"
        self.assertEqual(
            config.company_id.sudo().dte_pos_accounting_mode, "session_summary"
        )
