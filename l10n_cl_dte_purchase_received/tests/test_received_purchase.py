# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_inbox_account.services.common import (
    BINDING,
    IncomingAccountError,
)

from .common import ReceivedPurchaseCase


@tagged("post_install", "-at_install")
class TestReceivedPurchase(ReceivedPurchaseCase):
    def test_received_46_becomes_customer_invoice_draft(self):
        source = self.purchase_source()
        self.assertEqual(source.document_code, "46")
        wizard = self.purchase_wizard(source)
        self.assertEqual(wizard.journal_id, self.sale_journal)
        self.assertEqual(wizard.source_vat, 190)
        self.assertEqual(wizard.source_total, 1000)
        move = self.customer_invoice(wizard)
        self.assertEqual(move.move_type, "out_invoice")
        self.assertEqual(move.state, "draft")
        self.assertEqual(move.auto_post, "no")
        self.assertEqual(move.partner_id, self.supplier)
        self.assertEqual(move.journal_id, self.sale_journal)
        self.assertEqual(move.l10n_latam_document_type_id.code, "46")
        self.assertEqual(move.l10n_latam_document_number.lstrip("0"), str(source.folio))
        self.assertEqual(move.amount_untaxed, 1000)
        self.assertEqual(move.amount_tax, 0)
        self.assertEqual(move.amount_total, 1000)
        self.assertEqual(move.invoice_line_ids.tax_ids, self.sale_vat | self.retention)
        self.assertEqual(move.dte_inbox_binding_ids.document_code, "46")
        self.assertEqual(move.dte_inbox_binding_ids.incoming_document_id, source)
        self.assert_entry(move)

    def assert_entry(self, move):
        lines = move.line_ids
        receivable = lines.filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        )
        self.assertEqual(sum(receivable.mapped("balance")), 1000)
        self.assertEqual(
            sum(
                lines.filtered(lambda line: line.tax_line_id == self.sale_vat).mapped(
                    "balance"
                )
            ),
            -190,
        )
        retained = lines.filtered(lambda line: line.tax_line_id == self.retention)
        self.assertEqual(retained.account_id, self.retained_account)
        self.assertEqual(sum(retained.mapped("balance")), 190)
        revenue = lines.filtered(lambda line: line.display_type == "product")
        self.assertEqual(revenue.account_id, self.revenue)
        self.assertEqual(sum(revenue.mapped("balance")), -1000)
        self.assertEqual(sum(lines.mapped("balance")), 0)

    def test_posting_rechecks_evidence_and_blocks_issuance(self):
        move = self.customer_invoice(self.purchase_wizard())
        move.action_post()
        self.assertEqual(move.state, "posted")
        self.assertTrue(move.name.endswith(move.l10n_latam_document_number))
        self.assertFalse(move.dte_issuance_available)
        with self.assertRaisesRegex(UserError, "cannot be issued again"):
            move.issue_dte()
        self.assert_entry(move)

    def test_unsupported_retention_codes_are_refused(self):
        self._map_partial_retentions(self.cattle_retention)
        cases = (
            (dict(retention_code="40"), "retention_partial"),
            (dict(retention_code="32"), "retention_unsupported"),
            (dict(retention_code="32", rate="8", retained="100"), "amount_mismatch"),
        )
        for kwargs, code in cases:
            with self.subTest(code=code):
                wizard = self.purchase_wizard(self.purchase_source(**kwargs))
                self.assertTrue(wizard.has_blockers)
                self.assertIn(code, wizard.diagnostic_text)

    def _map_partial_retentions(self, taxes):
        self.recipient.sudo().dte_inbox_account_received46_partial_tax_ids = taxes

    def test_received_46_with_partial_retention_is_booked(self):
        self._map_partial_retentions(self.cattle_retention)
        source = self.purchase_source(retention_code="32", rate="8")
        wizard = self.purchase_wizard(source)
        self.assertEqual(wizard.source_total, 1110)
        move = self.customer_invoice(wizard)
        self.assertEqual(
            move.invoice_line_ids.tax_ids, self.sale_vat | self.cattle_retention
        )
        self.assertEqual(move.amount_total, 1110)
        move.action_post()
        lines = move.line_ids
        receivable = lines.filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        )
        self.assertEqual(sum(receivable.mapped("balance")), 1110)
        retained = lines.filtered(
            lambda line: line.tax_line_id == self.cattle_retention
        )
        self.assertEqual(retained.account_id, self.retained_account)
        self.assertEqual(sum(retained.mapped("balance")), 80)
        self.assertEqual(
            sum(
                lines.filtered(lambda line: line.tax_line_id == self.sale_vat).mapped(
                    "balance"
                )
            ),
            -190,
        )

    def test_unmapped_partial_retention_is_refused(self):
        self._map_partial_retentions(self.cattle_retention)
        wizard = self.purchase_wizard(
            self.purchase_source(retention_code="34", rate="4")
        )
        self.assertTrue(wizard.has_blockers)
        self.assertIn("retention_unmapped", wizard.diagnostic_text)

    def test_partial_retention_taxes_are_validated(self):
        env = self.env(su=True)
        group = self.sale_vat.tax_group_id
        wrong_rate = self._tax(
            env, "Cattle at a wrong rate", -10, 32, self.retained_account, group
        )
        unsupported = self._tax(
            env, "Unsupported code", -19, 40, self.retained_account, group
        )
        duplicate = self.cattle_retention.copy(dict(name="Second cattle tax"))
        for taxes in (wrong_rate, unsupported, self.cattle_retention | duplicate):
            with self.subTest(taxes=taxes.mapped("name")):
                with self.assertRaises(IncomingAccountError) as error:
                    self._map_partial_retentions(taxes)
                self.assertEqual(
                    error.exception.diagnostic["code"], "received46_not_configured"
                )

    def test_retention_must_cover_the_vat(self):
        source = self.purchase_source(retained="100")
        wizard = self.purchase_wizard(source)
        self.assertTrue(wizard.has_blockers)
        self.assertIn("retention_unsupported", wizard.diagnostic_text)

    def test_missing_configuration_is_refused(self):
        source = self.purchase_source()
        self.recipient.sudo().dte_inbox_account_received46_retention_tax_id = False
        wizard = self.purchase_wizard(source)
        self.assertTrue(wizard.has_blockers)
        self.assertIn("received46_not_configured", wizard.diagnostic_text)
        unaccounted = self.retention.sudo().copy(dict(name="Retention without account"))
        unaccounted.invoice_repartition_line_ids.account_id = False
        with self.assertRaises(IncomingAccountError) as error:
            self.recipient.sudo().dte_inbox_account_received46_retention_tax_id = (
                unaccounted
            )
        self.assertEqual(
            error.exception.diagnostic["code"], "received46_not_configured"
        )
        with self.assertRaises(AccessError):
            self.recipient.with_user(
                self.operator
            ).dte_inbox_account_received46_retention_tax_id = self.retention

    def test_duplicates_are_refused(self):
        source = self.purchase_source()
        first = self.customer_invoice(self.purchase_wizard(source))
        self.assertEqual(source.action_prepare_customer_invoice()["res_id"], first.id)
        other = self.purchase_source()
        self.op_env["account.move"].create(
            dict(
                move_type="out_invoice",
                partner_id=self.supplier.id,
                journal_id=self.recipient_data["default_journal_sale"].id,
                invoice_date=other.issue_date,
                l10n_latam_document_type_id=first.l10n_latam_document_type_id.id,
                l10n_latam_document_number=str(other.folio),
                invoice_line_ids=[
                    Command.create(dict(name="Manual", quantity=1, price_unit=1000))
                ],
            )
        )
        wizard = self.purchase_wizard(other)
        self.assertTrue(wizard.has_blockers)
        self.assertIn("duplicate_invoice", wizard.diagnostic_text)
        self.assertEqual(
            self.env[BINDING]
            .sudo()
            .search_count([("incoming_document_id", "=", other.id)]),
            0,
        )

    def test_not_our_sale_is_not_booked(self):
        third = (
            self.env["res.partner"]
            .sudo()
            .create(
                dict(
                    name="Other seller",
                    vat="11111111-1",
                    country_id=self.chile.id,
                    street="Calle Tercero 300",
                    city="Santiago",
                )
            )
        )
        content = self.signed_purchase(receiver=third)
        with self.assertRaisesRegex(ValidationError, "receiver does not match"):
            self.source(content=content)
        source = self.purchase_source()
        source.sudo()._write_internal({"receiver_vat": "11111111-1"})
        wizard = self.purchase_wizard(source)
        self.assertTrue(wizard.has_blockers)
        self.assertIn("identity_mismatch", wizard.diagnostic_text)
        self.assertFalse(
            self.env["account.move"]
            .sudo()
            .search_count([("journal_id", "=", self.sale_journal.id)])
        )

    def test_unknown_or_archived_buyer_is_refused(self):
        source = self.purchase_source()
        self.supplier.sudo().active = False
        wizard = self.purchase_wizard(source)
        self.assertTrue(wizard.has_blockers)
        self.assertIn("customer_archived", wizard.diagnostic_text)

    def test_access_and_multi_company(self):
        source = self.purchase_source()
        for user in (self.reader, self.billing, self.dte_manager):
            with self.assertRaises(AccessError):
                source.with_user(user).action_prepare_customer_invoice()
        with self.assertRaises(AccessError):
            source.with_context(
                allowed_company_ids=[self.company.id]
            ).action_prepare_customer_invoice()
        wizard = self.purchase_wizard(source)
        with self.assertRaises(AccessError):
            wizard.with_user(self.manager).action_confirm()
        self.customer_invoice(wizard)

    def test_inbox_response_and_vendor_bills_unchanged(self):
        source = self.purchase_source()
        self.customer_invoice(self.purchase_wizard(source))
        action = source.with_user(self.dte_manager).action_create_response()
        self.assertEqual(action["res_model"], "l10n_cl.incoming.dte.response.wizard")
        bill = self.bill(self.wizard(self.source()))
        self.assertEqual(bill.move_type, "in_invoice")
        self.assertEqual(bill.amount_total, 1190)
        with self.assertRaises(UserError):
            self.op_env["l10n_cl.incoming.dte"].browse(
                self.source(content=self.signed()).id
            ).action_prepare_customer_invoice()

    def test_tax_choice_is_validated(self):
        wizard = self.purchase_wizard()
        wizard.line_ids.tax_id = self.purchase_vat
        wizard.action_refresh_preview()
        self.assertTrue(wizard.has_blockers)
        self.assertIn("tax_invalid", wizard.diagnostic_text)
        wizard.line_ids.tax_id = self.sale_vat
        wizard.action_refresh_preview()
        self.customer_invoice(wizard)
