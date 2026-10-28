# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo.tests import TransactionCase


class TestRcvCompanyIsolation(TransactionCase):
    def test_user_only_sees_allowed_company_periods(self):
        company_a = self.env.company
        company_b = self.env["res.company"].create({"name": "RCV test company B"})
        Period = self.env["l10n.cl.rcv.period"].sudo()
        Period.create(
            {
                "company_id": company_a.id,
                "year": 2026,
                "month": 1,
                "operation": "purchase",
            }
        )
        Period.create(
            {
                "company_id": company_b.id,
                "year": 2026,
                "month": 1,
                "operation": "purchase",
            }
        )
        group_user = self.env.ref("base.group_user")
        group_rcv = self.env.ref("l10n_cl_rcv.group_rcv_user")
        user = self.env["res.users"].create(
            {
                "name": "RCV reader",
                "login": "rcv-reader-test",
                "company_id": company_a.id,
                "company_ids": [(6, 0, [company_a.id])],
                "groups_id": [(6, 0, [group_user.id, group_rcv.id])],
            }
        )
        visible = Period.with_user(user).search([])
        self.assertTrue(visible)
        self.assertEqual(set(visible.mapped("company_id").ids), {company_a.id})
