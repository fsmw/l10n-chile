# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService

from ..services.common import MAPPING
from .common import IncomingAccountCase


@tagged("post_install", "-at_install")
class TestIncomingAccountSecurityEvidence(IncomingAccountCase):
    def test_role_matrix(self):
        source = self.source()
        for user in (self.reader, self.billing, self.dte_manager):
            with self.assertRaises(AccessError):
                source.with_user(user).action_prepare_vendor_bill()
        self.assertFalse(self.wizard(source).has_blockers)
        with self.assertRaises(AccessError):
            self.op_env[MAPPING].create({})
        self.assertTrue(self.mapping())

    def test_company_rules_and_owner(self):
        wizard = self.wizard()
        with self.assertRaises(AccessError):
            wizard.with_user(self.manager).action_confirm()
        with self.assertRaises(AccessError):
            wizard.incoming_document_id.with_context(
                allowed_company_ids=[self.company.id]
            ).action_prepare_vendor_bill()

    def test_narrow_xml_read(self):
        source = self.source()
        with self.assertRaises(AccessError):
            source.read(["raw_xml"])
        with self.assertRaises(AccessError):
            source.read(["diagnostics"])
        move = self.bill(self.wizard(source))
        self.assertEqual(move.amount_total, 1190)

    def test_immutable_source_and_snapshot(self):
        source = self.source()
        before = (
            source.sudo().raw_xml,
            source.xml_sha256,
            source.state,
            source.xml_validation_state,
            sorted(source.event_ids.ids),
        )
        move = self.bill(self.wizard(source))
        binding = move.dte_inbox_binding_ids
        with self.assertRaises(UserError):
            binding.sudo().write({"folio": "999"})
        with self.assertRaises(UserError):
            binding.sudo().unlink()
        self.assertEqual(
            before,
            (
                source.sudo().raw_xml,
                source.xml_sha256,
                source.state,
                source.xml_validation_state,
                sorted(source.event_ids.ids),
            ),
        )
        self.assertEqual(binding.xml_sha256, source.xml_sha256)

    def test_no_fiscal_side_effects(self):
        source = self.source()
        with (
            patch.object(
                SignatureService,
                "sign_document",
                side_effect=AssertionError("No new signature"),
            ),
            patch.object(
                type(source),
                "action_create_response",
                side_effect=AssertionError("No fiscal response"),
            ),
        ):
            move = self.bill(self.wizard(source))
            self.assertEqual(move.state, "draft")
        self.assertFalse(source.response_ids)

    def test_native_bill_permissions(self):
        wizard = self.wizard()
        original_create = type(self.op_env["account.move"]).create
        seen = []

        def record_create(records, vals_list):
            seen.append((records.env.uid, records.env.su))
            return original_create(records, vals_list)

        with patch.object(type(self.op_env["account.move"]), "create", record_create):
            self.bill(wizard)
        self.assertIn((self.operator.id, False), seen)
        self.assertNotIn((self.operator.id, True), seen)

    def test_settings_save_atomically(self):
        from odoo import Command
        from odoo.tests import new_test_user

        administrator = new_test_user(
            self.env(su=True),
            login="incoming_configuration_admin",
            groups="base.group_system,l10n_cl_dte_inbox_account.group_inbox_account_manager",
            company_id=self.recipient.id,
            company_ids=[Command.set([self.recipient.id])],
            context={"no_reset_password": True},
        )
        self.recipient.sudo().write(
            dict(
                dte_inbox_account_enabled=False,
                dte_inbox_account_journal_id=False,
                dte_inbox_account_vat_tax_id=False,
                dte_inbox_account_exempt_tax_id=False,
            )
        )
        settings = (
            self.op_env["res.config.settings"]
            .with_user(administrator)
            .create(
                dict(
                    company_id=self.recipient.id,
                    dte_inbox_account_enabled=True,
                    dte_inbox_account_journal_id=self.purchase_journal.id,
                    dte_inbox_account_vat_tax_id=self.purchase_vat.id,
                    dte_inbox_account_exempt_tax_id=self.purchase_exempt.id,
                )
            )
        )
        self.assertTrue(settings.dte_inbox_account_enabled)
        self.assertEqual(settings.dte_inbox_account_journal_id, self.purchase_journal)
        self.assertFalse(self.wizard().has_blockers)
