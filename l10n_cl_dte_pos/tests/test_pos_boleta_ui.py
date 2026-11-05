# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.addons.point_of_sale.tests.common import TestPointOfSaleCommon


class TestPosBoletaUi(TestPointOfSaleCommon):
    def test_company_policy_is_available_in_dte_settings(self):
        view = self.env.ref("l10n_cl_dte_pos.view_company_form_dte_pos")
        self.assertIn("dte_pos_accounting_mode", view.arch_db)

    def test_pos_payment_methods_expose_sii_mapping(self):
        field = self.env["pos.payment.method"]._fields[
            "l10n_cl_dte_boleta_payment_method"
        ]
        self.assertEqual(
            {key for key, _label in field.selection}, {"1", "2", "3", "4", "5"}
        )
