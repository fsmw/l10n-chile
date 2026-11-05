# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).


from odoo.exceptions import ValidationError
from odoo.tests import tagged

from ..services.common import BINDING
from .common import IncomingAccountCase


@tagged("post_install", "-at_install")
class TestIncomingAccountLinking(IncomingAccountCase):
    def test_existing_matching_draft(self):
        source = self.source()
        manual = self.manual_bill(source)
        before = [
            (line.id, line.quantity, line.price_unit, line.tax_ids.ids)
            for line in manual.invoice_line_ids
        ]
        linked = self.bill(self.link_wizard(source, manual))
        self.assertEqual(linked, manual)
        self.assertEqual(
            before,
            [
                (line.id, line.quantity, line.price_unit, line.tax_ids.ids)
                for line in manual.invoice_line_ids
            ],
        )
        self.assertEqual(linked.dte_inbox_binding_ids.operation, "link")

    def test_ambiguous_or_incompatible_draft(self):
        source = self.source()
        manual = self.manual_bill(source)
        wizard = self.link_wizard(source, manual)
        manual.invoice_line_ids[0].quantity += 1
        self.assert_code("amount_mismatch", wizard.action_confirm)
        self.assertFalse(manual.dte_inbox_binding_ids)

    def test_posted_cancelled_or_hidden_candidate(self):
        source = self.source()
        manual = self.manual_bill(source)
        manual.button_cancel()
        wizard = self.wizard(source, ready=False)
        self.assertTrue(wizard.has_blockers)
        self.assertIn("duplicate_bill", wizard.diagnostic_text)

    def test_repeat_confirmation(self):
        wizard = self.wizard()
        move = self.bill(wizard)
        self.assertEqual(self.bill(wizard), move)
        self.assertEqual(self.op_env[BINDING].search_count([]), 1)

    def test_cross_environment_identity(self):
        source = self.source()
        move = self.bill(self.wizard(source))
        source.sudo()._write_internal({"sii_environment": "certification"})
        with self.assertRaises(ValidationError):
            self.manual_bill(source)
        self.assertEqual(move.state, "draft")
