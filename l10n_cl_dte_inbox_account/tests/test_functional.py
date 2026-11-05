# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).


from odoo.tests import Form, tagged

from .common import IncomingAccountCase


@tagged("post_install", "-at_install")
class TestIncomingAccountFunctional(IncomingAccountCase):
    def test_form_preview_create_link(self):
        source = self.source()
        wizard = self.wizard(source)
        action = wizard.action_refresh_preview()
        self.assertEqual(action["name"], wizard.env._("Prepare Vendor Bill"))
        with Form(wizard) as preview:
            self.assertEqual(preview.document_code, "33")
            self.assertEqual(preview.source_total, 1190)
        move = self.bill(wizard)
        self.assertEqual(move.state, "draft")
        other = self.source("34")
        manual = self.manual_bill(other)
        link = self.link_wizard(other, manual)
        with Form(link) as preview:
            self.assertEqual(preview.mode, "link")
        self.assertEqual(self.bill(link), manual)

    def test_traceability_and_draft_edits(self):
        source = self.source()
        move = self.bill(self.wizard(source))
        self.assertEqual(move.action_open_incoming_dte()["res_id"], source.id)
        self.assertEqual(source.action_open_vendor_bill()["res_id"], move.id)
        other_account = self.expense.sudo().copy(
            {"name": "Alternative purchase expense", "code": "999765"}
        )
        move.invoice_line_ids[0].account_id = other_account
        move.action_post()
        self.assertEqual(move.state, "posted")
        self.assertEqual(move.dte_inbox_binding_ids.incoming_document_id, source)
