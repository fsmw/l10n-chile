# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "dte_pos_return")
class TestPosReturnScaffold(TransactionCase):
    def test_schema_and_disabled_default(self):
        """Confirm schema installation only; this is not refund acceptance."""
        self.assertIn("dte_return_target_document_id", self.env["pos.order"]._fields)
        self.assertIn(
            "note_pos_return_order_id", self.env["l10n_cl.electronic.document"]._fields
        )
        company = self.env["res.company"].create({"name": "POS Return Scaffold Schema"})
        self.assertFalse(company.dte_pos_returns_enabled)
