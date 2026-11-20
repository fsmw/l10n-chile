# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import date

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_inbox_account.services.common import BINDING

from ..services.rcv_draft_service import rcv_accounting_date
from .common import HEADER_SUPPLIER_RUT, UNKNOWN_SUPPLIER_RUT, RcvInboxCase


@tagged("post_install", "-at_install")
class TestRcvDrafts(RcvInboxCase):
    def draft_wizard(self, document):
        action = document.with_env(self.rcv_env).action_create_rcv_draft()
        return self.rcv_env[action["res_model"]].browse(action["res_id"])

    def test_draft_from_the_signed_xml_of_the_received_dte(self):
        source = self.received()
        document = self.rcv_document(source.folio)
        self.assertEqual(document.reconciliation_state, "missing")
        wizard = self.draft_wizard(document)
        self.assertEqual(wizard.incoming_dte_id, source)
        self.assertFalse(wizard.line_ids)
        action = wizard.action_confirm()
        preview = self.rcv_env[action["res_model"]].browse(action["res_id"])
        self.assertEqual(preview._name, "l10n_cl.incoming.dte.bill.wizard")
        self.assertEqual(preview.accounting_date, date(2026, 1, 15))
        self.assertFalse(preview.has_blockers, preview.diagnostic_text)
        move = self.rcv_env["account.move"].browse(preview.action_confirm()["res_id"])
        xml_lines = self.document(source)["lines"]
        self.assertEqual(
            move.invoice_line_ids.mapped("dte_inbox_line_number"),
            [line["ordinal"] for line in xml_lines],
        )
        self.assertEqual(move.invoice_line_ids.tax_ids, self.purchase_vat)
        self.assertEqual(
            (move.state, move.auto_post, move.amount_tax, move.amount_total),
            ("draft", "no", 190, 1190),
        )
        self.assertEqual(move.date, date(2026, 1, 15))
        binding = self.env[BINDING].sudo().search([("move_id", "=", move.id)])
        self.assertEqual(binding.incoming_document_id, source)
        self.assertEqual(document.move_id, move)
        self.assertEqual(document.reconciliation_state, "matched")

    def test_header_draft_from_the_rcv_amounts_without_xml(self):
        document = self.rcv_document(501, HEADER_SUPPLIER_RUT)
        wizard = self.draft_wizard(document)
        self.assertFalse(wizard.incoming_dte_id)
        self.assertEqual(wizard.partner_id, self.header_supplier)
        self.assertEqual(wizard.line_ids.component, "vat_amount")
        tax = wizard.line_ids.tax_ids
        # The one purchase tax tagged as recoverable VAT of the company.
        self.assertEqual(
            (tax.type_tax_use, tax.amount, tax.l10n_cl_sii_code), ("purchase", 19, 14)
        )
        self.assertEqual(wizard.line_ids.price_unit, 1000)
        move = self.rcv_env["account.move"].browse(wizard.action_confirm()["res_id"])
        self.assertEqual(
            (move.state, move.auto_post, move.move_type), ("draft", "no", "in_invoice")
        )
        self.assertEqual(move.partner_id, self.header_supplier)
        self.assertEqual(int(move.l10n_latam_document_number), 501)
        self.assertEqual((move.amount_untaxed, move.amount_tax), (1000, 190))
        self.assertEqual(move.date, date(2026, 1, 15))
        self.assertEqual(document.move_id, move)
        self.assertEqual(document.reconciliation_state, "matched")
        self.assertIn("draft_created", document.match_log_ids.mapped("event_type"))

    def test_header_draft_splits_the_rcv_vat_components_and_exempt(self):
        document = self.rcv_document(
            502, HEADER_SUPPLIER_RUT, net=1000, vat_amount=190, exempt=300
        )
        wizard = self.draft_wizard(document)
        self.assertEqual(
            sorted(wizard.line_ids.mapped("component")), ["exempt", "vat_amount"]
        )
        exempt = wizard.line_ids.filtered(lambda line: line.component == "exempt")
        self.assertFalse(exempt.tax_ids)
        exempt.tax_ids = wizard.line_ids.tax_ids
        with self.assertRaises(UserError):
            wizard.action_confirm()
        exempt.tax_ids = False
        move = self.rcv_env["account.move"].browse(wizard.action_confirm()["res_id"])
        self.assertEqual((move.amount_untaxed, move.amount_total), (1300, 1490))

    def test_accounting_date_follows_the_rcv_period(self):
        document = self.rcv_document(503, HEADER_SUPPLIER_RUT, date="2025-12-20")
        self.assertEqual(rcv_accounting_date(document), date(2026, 1, 31))
        move = self.rcv_env["account.move"].browse(
            self.draft_wizard(document).action_confirm()["res_id"]
        )
        self.assertEqual(move.invoice_date, date(2025, 12, 20))
        self.assertEqual(move.date, date(2026, 1, 31))

    def test_supplier_is_created_only_with_explicit_input(self):
        document = self.rcv_document(504, UNKNOWN_SUPPLIER_RUT)
        partners = self.env["res.partner"].sudo().with_context(active_test=False)
        wizard = self.draft_wizard(document)
        self.assertFalse(wizard.partner_candidate_ids)
        self.assertEqual(wizard.new_partner_name, document.issuer_name)
        with self.assertRaises(UserError):
            wizard.action_confirm()
        self.assertFalse(partners.search([("vat", "=", UNKNOWN_SUPPLIER_RUT)]))
        wizard.write({"create_partner": True, "new_partner_name": " "})
        with self.assertRaises(UserError):
            wizard.action_confirm()
        wizard.write({"new_partner_name": "Synthetic New Supplier"})
        move = self.rcv_env["account.move"].browse(wizard.action_confirm()["res_id"])
        partner = partners.search([("vat", "=", UNKNOWN_SUPPLIER_RUT)])
        self.assertEqual(partner.name, "Synthetic New Supplier")
        self.assertEqual(move.partner_id, partner)

    def test_no_duplicate_draft(self):
        document = self.rcv_document(505, HEADER_SUPPLIER_RUT)
        first, second = self.draft_wizard(document), self.draft_wizard(document)
        move = self.rcv_env["account.move"].browse(first.action_confirm()["res_id"])
        with self.assertRaises(UserError):
            second.action_confirm()
        with self.assertRaises(UserError):
            document.with_env(self.rcv_env).action_create_rcv_draft()
        moves = (
            self.env["account.move"]
            .sudo()
            .search([("partner_id", "=", self.header_supplier.id)])
            .filtered(lambda bill: int(bill.l10n_latam_document_number) == 505)
        )
        self.assertEqual(moves, move)

    def test_draft_refused_when_an_entry_exists(self):
        source = self.received()
        self.bound_bill(source)
        document = self.rcv_document(source.folio)
        self.assertEqual(document.reconciliation_state, "matched")
        with self.assertRaises(UserError):
            document.with_env(self.rcv_env).action_create_rcv_draft()

    def test_only_dte_managers_prepare_drafts(self):
        document = self.rcv_document(506, HEADER_SUPPLIER_RUT)
        reader_env = self.env(
            user=self.rcv_reader,
            context=dict(self.env.context, allowed_company_ids=[self.recipient.id]),
        )
        with self.assertRaises(AccessError):
            document.with_env(reader_env).action_create_rcv_draft()
        with self.assertRaises(AccessError):
            document.with_env(reader_env).action_link_rcv_incoming_dte()

    def test_manager_of_another_company_cannot_prepare_drafts(self):
        document = self.rcv_document(507, HEADER_SUPPLIER_RUT)
        other_env = self.env(
            user=self.other_manager,
            context=dict(self.env.context, allowed_company_ids=[self.company.id]),
        )
        with self.assertRaises(AccessError):
            document.with_env(other_env).action_create_rcv_draft()
