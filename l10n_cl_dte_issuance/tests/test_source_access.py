# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import Mock, patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_core.tests.common import DteTestCase

from ..services.dte_issuance_service import DteIssuanceService


@tagged("post_install", "-at_install")
class TestSourceAccess(DteTestCase):
    def setUp(self):
        super().setUp()
        # A core record exercises real ACLs/rules without depending on a source addon.
        # Fiscal work is stopped at the elevated boundary in these contract tests.
        self.source = self.create_document()
        self.service = DteIssuanceService()
        self.adapter = Mock()

    def assert_denied_before_elevation(self, source, exception=AccessError):
        for action in ("issue", "prepare"):
            with (
                self.subTest(action=action),
                patch.object(self.service, f"_{action}") as work,
            ):
                with self.assertRaises(exception):
                    getattr(self.service, action)(source, self.adapter, "33")
                work.assert_not_called()
        self.assertFalse(self.adapter.mock_calls)

    def deny_record(self, operation):
        return (
            self.env["ir.rule"]
            .sudo()
            .create(
                {
                    "name": "Synthetic source access restriction",
                    "model_id": self.env["ir.model"]._get_id(self.source._name),
                    "domain_force": repr([("id", "!=", self.source.id)]),
                    "perm_read": operation == "read",
                    "perm_write": operation == "write",
                    "perm_create": False,
                    "perm_unlink": False,
                }
            )
        )

    def test_read_acl_denied(self):
        self.env["ir.model.access"].sudo().search(
            [("model_id.model", "=", self.source._name)]
        ).write({"active": False})
        self.assertFalse(self.source.has_access("read"))
        self.assert_denied_before_elevation(self.source)

    def test_write_acl_denied_with_read_access(self):
        source = self.source.with_user(self.dte_user)
        self.assertTrue(source.has_access("read"))
        self.assertFalse(source.has_access("write"))
        self.assert_denied_before_elevation(source)

    def test_read_and_write_record_rules_are_enforced(self):
        for operation in ("read", "write"):
            with self.subTest(operation=operation):
                rule = self.deny_record(operation)
                self.assertFalse(self.source.has_access(operation))
                self.assert_denied_before_elevation(self.source)
                rule.unlink()

    def test_company_rule_denied(self):
        other = self.env["res.company"].sudo().create({"name": "Other source company"})
        source = (
            self.env[self.source._name]
            .sudo()
            .create(
                {
                    "name": "Other company source",
                    "company_id": other.id,
                    "document_code": "33",
                    "source_key": "source-access-other-company",
                    "sii_environment": "demo",
                }
            )
            .with_user(self.dte_manager)
            .with_context(allowed_company_ids=[self.company.id])
        )
        self.assertFalse(source.has_access("read"))
        self.assert_denied_before_elevation(source)

    def test_dte_role_still_required(self):
        self.dte_user.sudo().groups_id |= self.env.ref("base.group_partner_manager")
        self.dte_user.sudo().groups_id -= self.env.ref(
            "l10n_cl_dte_core.group_dte_user"
        )
        source = self.company.partner_id.with_user(self.dte_user)
        self.assertTrue(source.has_access("read"))
        self.assertTrue(source.has_access("write"))
        self.assert_denied_before_elevation(source)

    def test_accessible_source_without_optional_hook_reaches_elevated_work(self):
        self.assertFalse(hasattr(self.source, "_check_dte_source_access"))
        for action in ("issue", "prepare"):
            with (
                self.subTest(action=action),
                patch.object(self.service, f"_{action}") as work,
            ):
                getattr(self.service, action)(self.source, self.adapter, "33")
                elevated, adapter, code = work.call_args.args
                self.assertTrue(elevated.env.su)
                self.assertEqual(elevated.id, self.source.id)
                self.assertIs(adapter, self.adapter)
                self.assertEqual(code, "33")

    def test_optional_hook_uses_original_actor_and_propagates_failures(self):
        observations = []

        def check(source, code):
            observations.append((source.env.uid, source.env.su, code))
            raise UserError(source.env._("Synthetic source policy denial"))

        with patch.object(
            type(self.source), "_check_dte_source_access", check, create=True
        ):
            self.assert_denied_before_elevation(self.source, UserError)
        self.assertEqual(observations, [(self.dte_manager.id, False, "33")] * 2)

    def test_hook_cannot_override_denied_source_read(self):
        self.deny_record("read")
        hook = Mock()
        with patch.object(
            type(self.source), "_check_dte_source_access", hook, create=True
        ):
            self.assert_denied_before_elevation(self.source)
        hook.assert_not_called()
