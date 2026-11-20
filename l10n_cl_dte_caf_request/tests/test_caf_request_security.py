# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError
from odoo.tests import new_test_user

from .common import OTHER_COMPANY_RUT, CafRequestTestCase


class TestCafRequestSecurity(CafRequestTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.other_company = cls.env["res.company"].create(
            {
                "name": "Synthetic Second Company",
                "country_id": cls.env.ref("base.cl").id,
                "vat": OTHER_COMPANY_RUT,
            }
        )
        cls.other_company.sudo().write({"dte_sii_environment": "certification"})
        cls.other_manager = new_test_user(
            cls.env,
            login="caf-request-other-manager",
            groups="base.group_user,l10n_cl_dte_core.group_dte_manager",
            company_id=cls.other_company.id,
            company_ids=[(6, 0, [cls.other_company.id])],
        )

    def test_dte_user_reads_but_cannot_manage(self):
        request = self.new_request(approve=False)
        with self.sii(*self.success()) as transport:
            reader_request = request.with_user(self.dte_user)
            self.assertEqual(reader_request.state, "pending")
            with self.assertRaises(AccessError):
                self.Request.with_user(self.dte_user).create(
                    {"document_code": "33", "requested_quantity": 5}
                )
            with self.assertRaises(AccessError):
                reader_request.action_approve()
            request.action_approve()
            with self.assertRaises(AccessError):
                reader_request.action_execute()
            with self.assertRaises(AccessError):
                self.Rule.with_user(self.dte_user).create(
                    {
                        "document_code": "33",
                        "environment": "certification",
                    }
                )
        self.assertFalse(transport.calls)

    def test_evidence_files_are_restricted_to_managers(self):
        request = self.new_request()
        with self.sii(*self.success()):
            request.action_execute()
        evidence = request.evidence_ids.with_user(self.dte_user)
        self.assertTrue(evidence.response_digest)
        with self.assertRaises(AccessError):
            evidence.read(["caf_file"])
        self.assertTrue(request.evidence_ids.caf_file)

    def test_multi_company_isolation(self):
        request = self.new_request(approve=False)
        other_request = self.Request.with_user(self.other_manager).create(
            {
                "document_code": "33",
                "requested_quantity": 5,
                "environment": "certification",
            }
        )
        self.assertEqual(other_request.company_id, self.other_company)
        visible = self.Request.with_user(self.dte_manager).search([])
        self.assertIn(request, visible)
        self.assertNotIn(other_request, visible)
        with self.assertRaises(AccessError):
            other_request.with_user(self.dte_manager).read(["state"])
        other_rule = self.Rule.with_user(self.other_manager).create(
            {"document_code": "33", "environment": "certification"}
        )
        self.assertNotIn(other_rule, self.Rule.with_user(self.dte_manager).search([]))
