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
class TestDteProductionReadinessChecks(SigningTestCase):
    """Exercise local readiness checks with synthetic credentials and CAF data."""

    def configure_production_identity(self):
        self.company.partner_id.sudo().write({"vat": "12345678-5"})
        self.company.sudo().write(
            {
                "dte_sii_sender_vat": "12345678-5",
                "dte_sii_resolution_number": "80",
                "dte_sii_resolution_date": fields.Date.today(),
            }
        )
        self.create_signing_profile(self.company, "production")

    def create_production_caf(self, code="33"):
        content = self.build_caf_xml(
            document_code=code,
            folio_start=100,
            folio_end=102,
            issuer_vat=self.company.partner_id.vat,
        )
        caf = (
            self.env["l10n_cl.caf.authorization"]
            .with_user(self.dte_manager)
            .create(
                {
                    "name": f"Synthetic production CAF {code}",
                    "company_id": self.company.id,
                    "sii_environment": "production",
                    "document_code": code,
                    "issuer_vat": self.company.partner_id.vat,
                    "folio_start": 100,
                    "folio_end": 102,
                    "date_from": fields.Date.today(),
                    "caf_file": base64.b64encode(content),
                }
            )
        )
        caf.action_validate_authorization()
        return caf

    def create_wizard(self, codes=("33",)):
        wizard = (
            self.env["l10n_cl.dte.production.readiness.wizard"]
            .with_user(self.dte_manager)
            .create({"company_id": self.company.id})
        )
        self.env["l10n_cl.dte.production.readiness.scope"].with_user(
            self.dte_manager
        ).create([{"wizard_id": wizard.id, "document_code": code} for code in codes])
        return wizard

    def test_missing_prerequisites_are_reported_as_blockers(self):
        wizard = self.create_wizard(codes=())
        wizard.action_check_readiness()
        self.assertEqual(wizard.result, "blocked")
        self.assertTrue(
            {"sii_resolution", "production_signer", "document_scope"}.issubset(
                set(wizard.diagnostic_line_ids.mapped("code"))
            )
        )

    def test_each_selected_dte_type_requires_a_production_caf(self):
        self.configure_production_identity()
        wizard = self.create_wizard(codes=("52",))
        wizard.action_check_readiness()
        line = wizard.diagnostic_line_ids.filtered(lambda item: item.code == "caf_52")
        self.assertEqual(line.level, "blocker")

    def test_ready_result_is_local_and_does_not_reserve_or_send(self):
        self.configure_production_identity()
        caf = self.create_production_caf()
        wizard = self.create_wizard()
        environment = self.company.dte_sii_environment
        with (
            patch.object(
                ProductionSiiTransport,
                "send_envelope",
                side_effect=AssertionError("readiness must not send"),
            ),
            patch.object(
                ProductionSiiTransport,
                "test_authentication",
                side_effect=AssertionError("readiness must not authenticate"),
            ),
            patch.object(
                ProductionSiiTransport,
                "query_envelope",
                side_effect=AssertionError("readiness must not query SII"),
            ),
        ):
            wizard.action_check_readiness()
        self.assertEqual(wizard.result, "ready")
        self.assertEqual(self.company.dte_sii_environment, environment)
        self.assertFalse(caf.folio_ids)

    def test_non_manager_cannot_create_readiness_wizard(self):
        with self.assertRaises(AccessError):
            self.env["l10n_cl.dte.production.readiness.wizard"].with_user(
                self.dte_user
            ).create({"company_id": self.company.id})
