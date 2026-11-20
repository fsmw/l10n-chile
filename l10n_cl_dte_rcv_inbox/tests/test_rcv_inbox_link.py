# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from .common import HEADER_SUPPLIER_RUT, RcvInboxCase


@tagged("post_install", "-at_install")
class TestRcvInboxLink(RcvInboxCase):
    def test_rcv_read_links_a_received_dte(self):
        source = self.received()
        document = self.rcv_document(source.folio)
        self.assertEqual(document.incoming_dte_id, source)
        self.assertEqual(source.sudo().rcv_document_ids, document)

    def test_received_dte_links_a_stored_rcv_purchase(self):
        # The next synthetic DTE takes the next folio of the counter.
        document = self.rcv_document(self.counter + 10)
        self.assertFalse(document.incoming_dte_id)
        source = self.received()
        self.assertEqual(source.folio, self.counter)
        self.assertEqual(document.incoming_dte_id, source)
        self.assertEqual(source.sudo().rcv_document_ids, document)

    def test_link_requires_the_same_issuer_and_company(self):
        other_issuer = self.rcv_document(self.counter + 10, HEADER_SUPPLIER_RUT)
        foreign = (
            self.env["l10n_cl.dte.rcv.document"]
            .sudo()
            .create(
                {
                    "company_id": self.company.id,
                    "operation": "purchase",
                    "tax_period": "202601",
                    "document_type_code": "33",
                    "issuer_vat": self.supplier.vat,
                    "folio": str(self.counter + 10),
                }
            )
        )
        source = self.received()
        self.assertFalse(other_issuer.incoming_dte_id)
        self.assertFalse(foreign.incoming_dte_id)
        self.assertFalse(source.sudo().rcv_document_ids)

    def test_manager_finds_the_received_dte_again(self):
        source = self.received()
        document = self.rcv_document(source.folio)
        document.sudo().incoming_dte_id = False
        document.with_env(self.rcv_env).action_link_rcv_incoming_dte()
        self.assertEqual(document.incoming_dte_id, source)

    def test_bound_bill_is_a_candidate_and_reconciles(self):
        source = self.received()
        bill = self.bound_bill(source)
        document = self.rcv_document(source.folio)
        self.assertEqual(document._get_rcv_inbox_bound_moves(), bill)
        self.assertIn(bill, document._get_rcv_candidate_moves())
        self.assertEqual(document.move_id, bill)
        self.assertEqual(document.reconciliation_state, "matched")

    def test_confirming_the_inbox_preview_links_the_rcv_purchase(self):
        source = self.received()
        document = self.rcv_document(source.folio)
        self.assertEqual(document.reconciliation_state, "missing")
        # A DTE manager confirming the inbox preview reconciles the purchase.
        action = source.with_env(self.rcv_env).action_prepare_vendor_bill()
        wizard = self.rcv_env[action["res_model"]].browse(action["res_id"])
        wizard.write({"accounting_date": "2026-01-15"})
        wizard.action_refresh_preview()
        self.assertFalse(wizard.has_blockers, wizard.diagnostic_text)
        bill = self.rcv_env["account.move"].browse(wizard.action_confirm()["res_id"])
        self.assertEqual(document.move_id, bill)
        self.assertEqual(document.reconciliation_state, "matched")
