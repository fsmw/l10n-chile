# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_signing.tests.common import SigningTestCase

from ..services.production_transport import ProductionSiiTransport


@tagged("post_install", "-at_install")
class TestDteProductionReadiness(SigningTestCase):
    """Test local production setup checks without contacting the SII."""

    def create_wizard(self, document_codes=("33",)):
        wizard = (
            self.env["l10n_cl.dte.production.readiness.wizard"]
            .with_user(self.dte_manager)
            .with_company(self.company)
            .create({"company_id": self.company.id})
        )
        self.env["l10n_cl.dte.production.readiness.scope"].with_user(
            self.dte_manager
        ).create(
            [{"wizard_id": wizard.id, "document_code": code} for code in document_codes]
        )
        return wizard

    def configure_company_production(self):
        self.company.partner_id.sudo().write({"vat": "12345678-5"})
        self.company.sudo().write(
            {
                "dte_sii_sender_vat": "12345678-5",
                "dte_sii_resolution_number": "80",
                "dte_sii_resolution_date": fields.Date.today(),
            }
        )
        self.create_signing_profile(company=self.company, environment="production")

    def create_production_caf(self, code="33", folio_start=100):
        caf_data = self.build_caf_xml(
            document_code=code,
            folio_start=folio_start,
            folio_end=folio_start + 2,
            issuer_vat=self.company.partner_id.vat,
        )
        authorization = (
            self.env["l10n_cl.caf.authorization"]
            .with_user(self.dte_manager)
            .with_company(self.company)
            .create(
                {
                    "name": f"Synthetic production CAF {code}",
                    "company_id": self.company.id,
                    "sii_environment": "production",
                    "is_synthetic": False,
                    "document_code": code,
                    "issuer_vat": self.company.partner_id.vat,
                    "folio_start": folio_start,
                    "folio_end": folio_start + 2,
                    "date_from": fields.Date.today(),
                    "caf_file": base64.b64encode(caf_data),
                }
            )
        )
        authorization.action_validate_authorization()
        return authorization

    def test_missing_production_signing_profile_blocks_readiness(self):
        wizard = self.create_wizard()
        wizard.action_check_readiness()
        self.assertEqual(wizard.result, "blocked")
        self.assertIn("production_signer", wizard.diagnostic_line_ids.mapped("code"))

    def test_requested_types_need_valid_production_cafs(self):
        self.configure_company_production()
        self.create_production_caf(code="33")
        wizard = self.create_wizard(document_codes=("52",))
        wizard.action_check_readiness()
        caf_finding = wizard.diagnostic_line_ids.filtered(
            lambda line: line.code == "caf_52"
        )
        self.assertEqual(caf_finding.level, "blocker")

    def test_valid_profile_and_available_caf_are_locally_ready(self):
        self.configure_company_production()
        caf = self.create_production_caf()
        wizard = self.create_wizard()
        original_environment = self.company.dte_sii_environment
        with (
            patch.object(
                ProductionSiiTransport,
                "send_envelope",
                side_effect=AssertionError("readiness check must not send"),
            ),
            patch.object(
                ProductionSiiTransport,
                "test_authentication",
                side_effect=AssertionError("readiness check must not authenticate"),
            ),
            patch.object(
                ProductionSiiTransport,
                "query_envelope",
                side_effect=AssertionError("readiness check must not query SII"),
            ),
        ):
            wizard.action_check_readiness()
        self.assertEqual(
            wizard.result,
            "ready",
            wizard.diagnostic_line_ids.mapped("message"),
        )
        self.assertTrue(wizard.checked_at)
        self.assertEqual(self.company.dte_sii_environment, original_environment)
        self.assertEqual(caf._next_available_folio(), caf.folio_start)
        self.assertFalse(caf.folio_ids)

    def test_missing_resolution_blocks_readiness(self):
        self.configure_company_production()
        self.company.sudo().write({"dte_sii_resolution_number": False})
        wizard = self.create_wizard()
        wizard.action_check_readiness()
        self.assertIn("sii_resolution", wizard.diagnostic_line_ids.mapped("code"))

    def test_signer_rut_must_match_authorized_sender(self):
        self.configure_company_production()
        self.company.sudo().write({"dte_sii_sender_vat": "11111111-1"})
        wizard = self.create_wizard()
        wizard.action_check_readiness()
        self.assertIn(
            "production_signer_rut", wizard.diagnostic_line_ids.mapped("code")
        )

    def test_empty_document_scope_blocks_readiness(self):
        self.configure_company_production()
        wizard = self.create_wizard(document_codes=())
        wizard.action_check_readiness()
        self.assertIn("document_scope", wizard.diagnostic_line_ids.mapped("code"))

    def test_non_manager_cannot_create_readiness_wizard(self):
        with self.assertRaises(AccessError):
            self.env["l10n_cl.dte.production.readiness.wizard"].with_user(
                self.dte_user
            ).create({"company_id": self.company.id})

    def test_configuration_actions_are_filtered_to_company_and_production(self):
        wizard = self.create_wizard()
        profile_action = wizard.action_open_signing_profiles()
        caf_action = wizard.action_open_caf_authorizations()
        self.assertEqual(
            profile_action["domain"],
            [
                ("company_id", "=", self.company.id),
                ("sii_environment", "=", "production"),
            ],
        )
        self.assertEqual(caf_action["domain"], profile_action["domain"])
