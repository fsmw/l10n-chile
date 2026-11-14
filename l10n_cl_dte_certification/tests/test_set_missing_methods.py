# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

BUSINESS_ERRORS = (AccessError, UserError, ValidationError)
NO_ARGUMENT_METHODS = (
    "_check_certification_access",
    "_check_signing_upload_access",
    "_check_can_add_caf",
    "_check_no_active_caf_requests",
    "_get_folio_requirements",
    "_get_sii_advance_result",
    "_lock_configuration",
    "_verify_source_integrity",
)


@tagged("post_install", "-at_install")
class TestCertificationSetMissingMethods(TransactionCase):
    """Smoke tests: methods that 18.0 certification flows call must exist and run."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.set = cls.env["l10n_cl.dte.certification.set"]._create_from_import(
            cls.company,
            "missing-methods.txt",
            base64.b64encode(b"synthetic SII SET source for smoke tests\n"),
        )
        cls.manager = cls.env["res.users"].create(
            {
                "name": "DTE certification smoke manager",
                "login": "dte-certification-smoke-manager",
                "company_id": cls.company.id,
                "company_ids": [(6, 0, cls.company.ids)],
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            cls.env.ref("base.group_user").id,
                            cls.env.ref("l10n_cl_dte_core.group_dte_manager").id,
                        ],
                    )
                ],
            }
        )
        cls.set = cls.set.with_user(cls.manager)

    def _run(self, method, *args):
        try:
            return method(*args)
        except BUSINESS_ERRORS:
            return None

    def test_no_argument_methods_exist_and_run(self):
        for name in NO_ARGUMENT_METHODS:
            with self.subTest(method=name):
                self._run(getattr(self.set, name))

    def test_document_code_methods_exist_and_run(self):
        self._run(self.set._cafs_for_document_code, "33")
        self._run(self.set._caf_for_document_code, "33")
        self._run(self.set._caf_with_available_folio, "33")

    def test_completion_review_and_receiver_activities_exist_and_run(self):
        self._run(self.set._mark_completion_review, {})
        self._run(self.set._populate_test_receiver_activities)
