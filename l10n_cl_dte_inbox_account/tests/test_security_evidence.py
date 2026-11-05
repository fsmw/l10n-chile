# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).


from odoo.tests import tagged

from .common import IncomingAccountCase


@tagged("post_install", "-at_install")
class TestIncomingAccountSecurityEvidence(IncomingAccountCase):
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
