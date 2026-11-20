# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.l10n_cl_dte_inbox_account.services.common import BINDING

from .common import HEADER_SUPPLIER_RUT, UNKNOWN_SUPPLIER_RUT, RcvInboxCase


@tagged("post_install", "-at_install")
class TestRcvAutoDrafts(RcvInboxCase):
    def run_cron(self):
        documents = self.env["l10n_cl.dte.rcv.document"].sudo()
        return documents._cron_create_rcv_drafts()

    def purchases(self):
        source = self.received()
        sync = self.read_rcv(
            [
                self.row(source.folio),
                self.row(601, HEADER_SUPPLIER_RUT),
                self.row(602, UNKNOWN_SUPPLIER_RUT),
            ]
        )
        by_folio = {document.folio: document for document in sync.document_ids}
        return source, by_folio[str(source.folio)], by_folio["601"], by_folio["602"]

    def test_company_needs_a_valid_draft_user(self):
        with self.assertRaises(ValidationError):
            self.recipient.sudo().write({"dte_rcv_auto_draft": True})
        with self.assertRaises(ValidationError):
            self.enable_auto_drafts(user=self.rcv_reader)
        with self.assertRaises(ValidationError):
            self.enable_auto_drafts(user=self.other_manager)

    def test_no_draft_without_the_company_opt_in(self):
        _source, with_xml, header, _unknown = self.purchases()
        self.assertEqual(self.run_cron(), 0)
        self.assertFalse(with_xml.move_id or header.move_id)
        self.assertEqual(header.reconciliation_state, "missing")

    def test_cron_prepares_drafts_once_and_never_posts(self):
        source, with_xml, header, unknown = self.purchases()
        self.enable_auto_drafts()
        partners = self.env["res.partner"].sudo().with_context(active_test=False)
        partner_count = partners.search_count([])
        self.assertEqual(self.run_cron(), 2)
        xml_move, header_move = with_xml.move_id, header.move_id
        binding = self.env[BINDING].sudo().search([("move_id", "=", xml_move.id)])
        self.assertEqual(binding.incoming_document_id, source)
        self.assertEqual(header_move.partner_id, self.header_supplier)
        for move in xml_move | header_move:
            self.assertEqual((move.state, move.auto_post), ("draft", "no"))
            self.assertEqual(move.create_uid, self.rcv_manager)
        self.assertEqual(with_xml.reconciliation_state, "matched")
        self.assertEqual(header.reconciliation_state, "matched")
        self.assertFalse(unknown.move_id)
        self.assertIn(UNKNOWN_SUPPLIER_RUT, unknown.dte_rcv_draft_error)
        self.assertEqual(partners.search_count([]), partner_count)
        moves = self.env["account.move"].sudo()
        move_count = moves.search_count([])
        self.assertEqual(self.run_cron(), 0)
        self.assertEqual(moves.search_count([]), move_count)

    def test_cron_skips_other_companies(self):
        _source, with_xml, header, _unknown = self.purchases()
        foreign = (
            self.env["l10n_cl.dte.rcv.document"]
            .sudo()
            .create(
                {
                    "company_id": self.company.id,
                    "operation": "purchase",
                    "tax_period": "202601",
                    "document_type_code": "33",
                    "issuer_vat": HEADER_SUPPLIER_RUT,
                    "folio": "603",
                    "sii_state": "REGISTRO",
                    "net_amount": 1000,
                    "vat_amount": 190,
                    "total_amount": 1190,
                }
            )
        )
        foreign.sudo().write({"reconciliation_state": "missing"})
        self.enable_auto_drafts()
        self.run_cron()
        self.assertTrue(with_xml.move_id and header.move_id)
        self.assertFalse(foreign.move_id)

    def test_unreviewed_received_dte_is_not_replaced_by_a_header_draft(self):
        source = self.received(reviewed=False)
        document = self.rcv_document(source.folio)
        self.enable_auto_drafts()
        self.assertEqual(self.run_cron(), 0)
        self.assertFalse(document.move_id)
        self.assertTrue(document.dte_rcv_draft_error)
        self.assertEqual(document.reconciliation_state, "missing")

    @mute_logger("odoo.addons.l10n_cl_dte_rcv_inbox.models.rcv_document")
    def test_invalid_draft_user_skips_the_company(self):
        _source, with_xml, header, _unknown = self.purchases()
        self.enable_auto_drafts()
        self.rcv_manager.sudo().active = False
        self.assertEqual(self.run_cron(), 0)
        self.assertFalse(with_xml.move_id or header.move_id)
