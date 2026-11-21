# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from .common import FactoringTestCase


@tagged("post_install", "-at_install")
class TestAssignmentAccess(FactoringTestCase):
    def test_dte_users_read_but_cannot_act(self):
        assignment = self.signed_assignment()
        self.dte_user.sudo().write(
            {"groups_id": [(4, self.env.ref("account.group_account_invoice").id)]}
        )
        as_user = assignment.with_user(self.dte_user)

        self.assertEqual(as_user.state, "signed")
        self.assertTrue(as_user.evidence_ids.sha256)
        for action in ("action_send", "action_cancel", "action_build_aec"):
            with self.assertRaises(AccessError):
                getattr(as_user, action)()
        with self.assertRaises(AccessError):
            self.new_assignment(user=self.dte_user)
        with self.assertRaises(AccessError):
            as_user.evidence_ids.read(["content"])
        self.assertFalse(self.invoice.with_user(self.dte_user).dte_assignment_available)

    def test_users_without_dte_group_cannot_read(self):
        assignment = self.signed_assignment()
        accountant = new_test_user(
            self.env(su=True),
            login="aec-accountant",
            groups="base.group_user,account.group_account_invoice",
            company_id=self.company.id,
            company_ids=[(6, 0, [self.company.id])],
        )
        with self.assertRaises(AccessError):
            assignment.with_user(accountant).read(["state"])

    def test_other_company_cannot_see_assignments(self):
        assignment = self.signed_assignment()
        other = self.env["res.company"].sudo().create({"name": "Otra Empresa"})
        manager = new_test_user(
            self.env(su=True),
            login="aec-other-manager",
            groups="base.group_user,l10n_cl_dte_core.group_dte_manager",
            company_id=other.id,
            company_ids=[(6, 0, [other.id])],
        )
        env = self.env(user=manager, context={"allowed_company_ids": [other.id]})
        model = env["l10n_cl.dte.assignment"]
        self.assertFalse(model.search([("id", "=", assignment.id)]))
        with self.assertRaises(AccessError):
            model.browse(assignment.id).read(["state"])
        evidence = env["l10n_cl.dte.assignment.evidence"]
        self.assertFalse(evidence.search([("assignment_id", "=", assignment.id)]))
