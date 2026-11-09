# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestDteProductionReadiness(TransactionCase):
    """Scaffold for local-only production prerequisite checks."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.manager = new_test_user(
            cls.env,
            login="dte-readiness-manager",
            groups="base.group_user,l10n_cl_dte_core.group_dte_manager",
            company_id=cls.company.id,
            company_ids=[(6, 0, [cls.company.id])],
        )

    def test_missing_production_signing_profile_blocks_readiness(self):
        self.skipTest("TODO(oca-implement): cover missing profile [blueprint §10]")

    def test_production_caf_coverage_is_checked_per_enabled_type(self):
        self.skipTest("TODO(oca-implement): cover CAF readiness [blueprint §10]")

    def test_invalid_or_inactive_production_signer_blocks_readiness(self):
        self.skipTest("TODO(oca-implement): cover invalid signer [blueprint §10]")

    def test_expired_or_exhausted_caf_blocks_readiness(self):
        self.skipTest(
            "TODO(oca-implement): cover expired and exhausted CAF [blueprint §10]"
        )

    def test_readiness_check_has_no_external_or_fiscal_side_effects(self):
        self.skipTest("TODO(oca-implement): prove local-only checks [blueprint §10]")

    def test_only_dte_managers_can_open_readiness_wizard(self):
        self.skipTest("TODO(oca-implement): cover minimum permissions [blueprint §10]")

    def test_configuration_actions_are_company_and_environment_scoped(self):
        self.skipTest(
            "TODO(oca-implement): cover scoped configuration navigation [blueprint §10]"
        )

    def test_readiness_does_not_claim_sii_approval(self):
        self.skipTest(
            "TODO(oca-implement): verify local-only result language [blueprint §10]"
        )
