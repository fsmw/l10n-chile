# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from ..services.rcv_matcher import RcvMatcher
from .common import CSV_HEADER, SUPPLIER_RUT, RcvMatchingTestCase


@tagged("post_install", "-at_install")
class TestRcvMatchingAccess(RcvMatchingTestCase):
    def _link_wizard(self, document, mode="link", user=None, **values):
        env = self.env(user=user or self.manager)
        return env["l10n_cl.dte.rcv.link.wizard"].create(
            {"document_id": document.id, "mode": mode, **values}
        )

    def test_manual_link_and_unlink_are_logged_and_respected(self):
        first = self._bill("100", post=False)
        second = self._bill("100", post=False)
        document = self._document(
            self._read([self._section([self._row(100, SUPPLIER_RUT)])]), 100
        )
        self.assertEqual(document.reconciliation_state, "duplicate")
        self._link_wizard(
            document, move_id=second.id, reason="Checked with the supplier."
        ).action_confirm()
        self.assertEqual(document.move_id, second)
        self.assertEqual(document.manual_link_state, "linked")
        self.assertEqual(document.reconciliation_state, "matched")
        RcvMatcher(self.env).reconcile(document)
        self.assertEqual(document.move_id, second)
        self.assertEqual(document.reconciliation_state, "matched")
        manual = document.match_log_ids.filtered(
            lambda log: log.event_type == "manual_link"
        )
        self.assertEqual(
            (manual.move_id, manual.user_id, manual.reason),
            (second, self.manager, "Checked with the supplier."),
        )
        self._link_wizard(
            document, mode="unlink", reason="Wrong bill."
        ).action_confirm()
        self.assertFalse(document.move_id)
        self.assertEqual(document.reconciliation_state, "review")
        self.assertEqual(document.reconciliation_reason_code, "manually_unlinked")
        RcvMatcher(self.env).reconcile(document)
        self.assertFalse(document.move_id)
        self.assertEqual(document.reconciliation_reason_code, "manually_unlinked")
        unlink = document.match_log_ids.filtered(
            lambda log: log.event_type == "manual_unlink"
        )
        self.assertEqual(unlink.previous_move_id, second)
        self.assertNotIn(first, document.match_log_ids.move_id)

    def test_manual_link_requires_the_fiscal_identity(self):
        self._bill("101")
        other = self._bill("102")
        document = self._document(
            self._read([self._section([self._row(101, SUPPLIER_RUT)])]), 101
        )
        wizard = self._link_wizard(document, move_id=other.id, reason="Guess.")
        with self.assertRaises(UserError):
            wizard.action_confirm()

    def test_reader_reads_but_cannot_reconcile_or_link(self):
        self._bill("110")
        document = self._document(
            self._read([self._section([self._row(110, SUPPLIER_RUT)])]), 110
        )
        as_reader = document.with_user(self.reader)
        self.assertEqual(as_reader.reconciliation_state, "matched")
        self.assertTrue(as_reader.match_log_ids)
        with self.assertRaises(AccessError):
            as_reader.action_reconcile()
        with self.assertRaises(AccessError):
            as_reader.action_link_move()
        with self.assertRaises(AccessError):
            self.env["l10n_cl.dte.rcv.match.log"].with_user(self.reader).check_access(
                "create"
            )
        with self.assertRaises(AccessError):
            self.env["l10n_cl.dte.rcv.match.log"].with_user(self.manager).check_access(
                "write"
            )
        action = document.with_user(self.manager).action_reconcile()
        self.assertEqual(action["params"]["type"], "success")

    def test_other_company_entries_and_logs_are_isolated(self):
        other_company = self.setup_other_company(name="RCV Other Company")["company"]
        other_company.vat = "76543210-3"
        other_journal = self.env["account.journal"].search(
            [("company_id", "=", other_company.id), ("type", "=", "purchase")], limit=1
        )
        other_bill = (
            self.env["account.move"]
            .with_company(other_company)
            .create(
                {
                    "move_type": "in_invoice",
                    "company_id": other_company.id,
                    "journal_id": other_journal.id,
                    "partner_id": self.supplier.id,
                    "invoice_date": "2026-08-12",
                    "l10n_latam_document_type_id": self.document_types["33"].id,
                    "l10n_latam_document_number": "120",
                    "invoice_line_ids": [
                        Command.create(
                            {"name": "Other company", "quantity": 1, "price_unit": 1000}
                        )
                    ],
                }
            )
        )
        document = self._document(
            self._read([self._section([self._row(120, SUPPLIER_RUT)])]), 120
        )
        self.assertNotIn(other_bill, document.candidate_move_ids)
        self.assertEqual(document.reconciliation_state, "missing")
        other_reader = self._matching_user(
            "rcv-match-other-reader", self.group_user, companies=other_company
        )
        logs = self.env["l10n_cl.dte.rcv.match.log"].with_user(other_reader)
        self.assertFalse(logs.search([]))
        self.assertTrue(logs.with_user(self.reader).search([]))

    def test_csv_import_reconciles_as_the_manager(self):
        bill = self._bill("130")
        content = "\n".join(
            (
                CSV_HEADER,
                f"33,{SUPPLIER_RUT},Fake Supplier,130,2026-08-12,1000,0,190,0,0,1190",
                f"33,{SUPPLIER_RUT},Fake Supplier,131,2026-08-12,500,0,95,0,0,595",
                "33,76222222-9,Bad RUT,132,2026-08-12,1000,0,190,0,0,1190",
            )
        ).encode()
        wizard = (
            self.env(user=self.manager)["l10n_cl.dte.rcv.csv.import.wizard"]
            .with_company(self.company)
            .create(
                {
                    "company_id": self.company.id,
                    "year": 2026,
                    "month": 8,
                    "operation": "purchase",
                    "upload": base64.b64encode(content),
                    "filename": "rcv.csv",
                }
            )
        )
        sync = self.env["l10n_cl.dte.rcv.sync"].browse(wizard.action_import()["res_id"])
        self.assertEqual(sync.state, "partial")
        matched = self._document(sync, 130)
        self.assertEqual(matched.move_id, bill)
        self.assertEqual(matched.reconciliation_state, "matched")
        # The file had invalid rows: an absent entry is not concluded.
        unmatched = self._document(sync, 131)
        self.assertEqual(unmatched.reconciliation_state, "indeterminate")
        self.assertEqual(matched.match_log_ids.user_id, self.manager)

    def test_hook_without_accounting_access_keeps_documents_pending(self):
        self._bill("140")
        manager = self._matching_user("rcv-match-no-accounting", self.group_manager)
        sync = self._read(
            [self._section([self._row(140, SUPPLIER_RUT)])],
            env=self.env(user=manager),
        )
        document = self._document(sync, 140)
        self.assertEqual(document.reconciliation_state, "pending")
        self.assertFalse(document.match_log_ids)

    def test_period_reconcile_action(self):
        bill = self._bill("150", post=False)
        sync = self._read([self._section([self._row(150, SUPPLIER_RUT)])])
        sync.period_id.current_sync_id = sync
        document = self._document(sync, 150)
        bill.unlink()
        sync.period_id.with_user(self.manager).action_reconcile()
        self.assertEqual(document.reconciliation_state, "missing")
