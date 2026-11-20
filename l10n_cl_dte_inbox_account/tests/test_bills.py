# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import patch

from lxml import etree

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from ..services.binding_service import IncomingAccountBindingService
from ..services.common import BINDING
from .common import NS, IncomingAccountCase

SII = NS["s"]


@tagged("post_install", "-at_install")
class TestIncomingAccountBills(IncomingAccountCase):
    def test_preview_has_no_business_writes(self):
        source = self.source()
        counts = {
            name: self.env[name].sudo().search_count([])
            for name in (
                "res.partner",
                "product.product",
                "account.move",
                BINDING,
                "l10n_cl.incoming.dte.event",
            )
        }
        self.wizard(source)
        for name, count in counts.items():
            self.assertEqual(self.env[name].sudo().search_count([]), count, name)

    def test_create_draft_33_mixed(self):
        move = self.bill(self.wizard(self.source(mixed=True)))
        self.assertEqual(move.state, "draft")
        self.assertEqual(move.auto_post, "no")
        self.assertEqual(move.amount_untaxed, 1200)
        self.assertEqual(move.amount_tax, 190)
        self.assertEqual(move.amount_total, 1390)
        self.assertEqual(len(move.dte_inbox_binding_ids), 1)
        self.assertEqual(sum(move.line_ids.mapped("balance")), 0)

    def test_supplier_notes_become_vendor_refunds_and_debits(self):
        for code, move_type in (("61", "in_refund"), ("56", "in_invoice")):
            with self.subTest(code=code):
                source = self.source(content=self.signed_note(code))
                move = self.bill(self.wizard(source))
                self.assertEqual(move.move_type, move_type)
                self.assertEqual(move.l10n_latam_document_type_id.code, code)
                self.assertEqual(move.amount_total, 1190)
                self.assertEqual(move.state, "draft")

    def test_invoice_with_other_currency_is_booked_in_pesos(self):
        def add_other_currency(root):
            totals = root.find(
                f"{{{SII}}}Documento/{{{SII}}}Encabezado/{{{SII}}}Totales"
            )
            other = etree.Element(f"{{{SII}}}OtraMoneda")
            for name, value in (
                ("TpoMoneda", "DOLAR USA"),
                ("TpoCambio", "950"),
                ("MntNetoOtrMnda", "1.05"),
                ("IVAOtrMnda", "0.20"),
                ("MntTotOtrMnda", "1.25"),
            ):
                etree.SubElement(other, f"{{{SII}}}{name}").text = value
            totals.addnext(other)

        move = self.bill(self.wizard(self.source(transform=add_other_currency)))
        self.assertEqual(move.currency_id.name, "CLP")
        self.assertEqual(move.amount_total, 1190)

    def test_global_discount_is_a_mapped_negative_line(self):
        source = self.source(global_discount=100)
        move = self.bill(self.wizard(source))
        discount = move.invoice_line_ids.filtered(lambda line: line.price_unit < 0)
        self.assertEqual(discount.price_unit, -100)
        self.assertEqual(discount.dte_inbox_line_number, 9001)
        self.assertEqual(move.amount_untaxed, 900)
        self.assertEqual(move.amount_total, 1071)

    def test_create_draft_34(self):
        source = self.source("34")
        move = self.bill(self.wizard(source))
        self.assertEqual(move.l10n_latam_document_type_id.code, "34")
        self.assertEqual(move.l10n_latam_document_number, str(source.folio))
        self.assertEqual(move.amount_tax, 0)
        self.assertEqual(move.amount_total, 1000)
        self.assertEqual(move.state, "draft")

    def test_stale_preview_and_forged_fields(self):
        wizard = self.wizard()
        wizard.write({"source_total": 1, "has_blockers": False})
        wizard.line_ids[0].write({"source_quantity": "99", "source_amount": 1})
        move = self.bill(wizard)
        self.assertEqual(move.amount_total, 1190)
        wizard2 = self.wizard()
        wizard2.accounting_date = "2026-12-30"
        self.assert_code("stale_preview", wizard2.action_confirm)

    def test_atomic_rollback(self):
        wizard = self.wizard()
        count = self.op_env["account.move"].search_count([])
        with patch.object(
            IncomingAccountBindingService,
            "_create_binding",
            side_effect=ValidationError("forced final failure"),
        ):
            with self.assertRaises(ValidationError):
                wizard.action_confirm()
        self.assertEqual(self.op_env["account.move"].search_count([]), count)
        self.assertFalse(self.op_env[BINDING].search([]))

    def test_due_and_accounting_dates(self):
        def due(root):
            etree.SubElement(
                root.find("s:Documento/s:Encabezado/s:IdDoc", NS),
                "{{{}}}FchVenc".format(NS["s"]),
            ).text = "2026-12-15"

        source = self.source(transform=due)
        wizard = self.wizard(source)
        wizard.accounting_date = "2026-12-01"
        wizard.action_refresh_preview()
        move = self.bill(wizard)
        self.assertEqual(str(move.invoice_date), str(source.issue_date))
        self.assertEqual(str(move.invoice_date_due), "2026-12-15")
        self.assertEqual(str(move.date), "2026-12-01")

    def test_post_rechecks_economics(self):
        move = self.bill(self.wizard())
        line = move.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[0]
        line.price_unit += 1
        self.assert_code("amount_mismatch", move.action_post)
        self.assertEqual(move.state, "draft")
        line.price_unit -= 1
        move.action_post()
        self.assertEqual(move.state, "posted")

    def test_cancel_reset_and_copy(self):
        move = self.bill(self.wizard())
        binding = move.dte_inbox_binding_ids
        with self.assertRaises(UserError):
            move.unlink()
        move.button_cancel()
        self.assertEqual(move.dte_inbox_binding_ids, binding)
        move.button_draft()
        copy = move.copy()
        self.assertFalse(copy.dte_inbox_binding_ids)
        self.assertFalse(any(copy.invoice_line_ids.mapped("dte_inbox_line_number")))
        self.assertEqual(move.dte_inbox_binding_ids, binding)

    def test_forged_provenance(self):
        move = self.bill(self.wizard())
        with self.assertRaises(AccessError):
            move.invoice_line_ids[0].write({"dte_inbox_line_number": 99})
        with self.assertRaises(AccessError):
            self.op_env[BINDING].with_context(_incoming_account_capability=True).create(
                {}
            )
        with self.assertRaises(AccessError):
            self.supplier.with_env(self.op_env).write(
                {"dte_inbox_account_lock_revision": 0}
            )

    def test_unbound_native_flow(self):
        self.recipient.sudo().dte_inbox_account_enabled = False
        move = self.op_env["account.move"].create(
            dict(
                move_type="entry",
                journal_id=self.purchase_journal.id,
                company_id=self.recipient.id,
            )
        )
        move.ref = "Ordinary accounting edit"
        self.assertFalse(move.dte_inbox_binding_ids)
        self.assertEqual(move.ref, "Ordinary accounting edit")
        move.unlink()

    def test_balanced_extra_payable_cannot_change_source_totals(self):
        move = self.bill(self.wizard())
        payable = move.line_ids.filtered(
            lambda line: line.account_id.account_type == "liability_payable"
        )[0]
        # Balanced journal data can still disagree with the invoice's displayed total.
        self.op_env["account.move.line"].with_context(
            skip_invoice_sync=True, check_move_validity=False
        ).create(
            [
                dict(
                    move_id=move.id,
                    name="Extra synthetic cost",
                    display_type="cogs",
                    account_id=self.expense.id,
                    debit=1,
                    credit=0,
                    currency_id=move.currency_id.id,
                    amount_currency=1,
                ),
                dict(
                    move_id=move.id,
                    name="Extra synthetic liability",
                    display_type="payment_term",
                    account_id=payable.account_id.id,
                    partner_id=move.partner_id.id,
                    debit=0,
                    credit=1,
                    date_maturity=move.invoice_date_due,
                    currency_id=move.currency_id.id,
                    amount_currency=-1,
                ),
            ]
        )
        self.assertEqual(move.amount_total, 1190)
        self.assertEqual(sum(move.line_ids.mapped("balance")), 0)
        self.assert_code("amount_mismatch", move.action_post)
