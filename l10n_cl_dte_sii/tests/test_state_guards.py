# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestDteStateGuards(AccountTestInvoicingCommon):
    """Keep the fiscal lifecycle of a DTE in sync with the SII.

    Once the SII processed the document the local journal entry cannot be
    cancelled or reset to draft: the only way out is a reversal document.
    """

    @classmethod
    @AccountTestInvoicingCommon.setup_country("cl")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.env.user.write(
            {"groups_id": [(4, cls.env.ref("l10n_cl_dte_sii.group_dte_user").id)]}
        )
        cls.partner = cls.partner_a
        cls.partner.write(
            {
                "country_id": cls.company.country_id.id,
                "vat": "76543210-3",
                "l10n_cl_sii_taxpayer_type": "1",
            }
        )
        cls.journal = cls.company_data["default_journal_sale"]
        # A sale journal without document types, used for non-fiscal invoices.
        cls.plain_journal = cls.journal.copy(
            {
                "name": "Ventas sin documentos",
                "code": "VSND",
                "l10n_latam_use_documents": False,
            }
        )

    def _make_invoice(
        self, document_type_code="33", status=None, company=None, journal=None
    ):
        company = company or self.company
        journal = journal or self.journal
        document_type = self.env["l10n_latam.document.type"]
        if document_type_code:
            document_type = document_type.search(
                [
                    ("country_id.code", "=", "CL"),
                    ("code", "=", document_type_code),
                ],
                limit=1,
            )
            self.assertTrue(
                document_type, f"Missing Chilean DTE type {document_type_code}"
            )
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": company.id,
                "journal_id": journal.id,
                "partner_id": self.partner.id,
                "invoice_date": "2026-10-28",
                "l10n_latam_document_type_id": document_type.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Servicio de prueba",
                            "quantity": 1,
                            "price_unit": 1000,
                            "account_id": self.company_data[
                                "default_account_revenue"
                            ].id,
                        },
                    )
                ],
            }
        )
        move.with_context(l10n_cl_skip_dte_emission=True).action_post()
        if status:
            move._l10n_cl_write_dte_lifecycle_values({"l10n_cl_dte_status": status})
        return move

    def test_cancel_blocked_for_accepted_and_objected(self):
        for status in ("accepted", "objected"):
            with self.subTest(status=status):
                move = self._make_invoice(status=status)
                before = move.l10n_cl_dte_status
                with self.assertRaisesRegex(UserError, "No se puede cancelar"):
                    move.button_cancel()
                self.assertEqual(move.state, "posted")
                self.assertEqual(move.l10n_cl_dte_status, before)

    def test_cancel_blocked_while_asking_for_status(self):
        move = self._make_invoice(status="ask_for_status")
        with self.assertRaisesRegex(UserError, "consulta de estado"):
            move.button_cancel()
        self.assertEqual(move.state, "posted")
        self.assertEqual(move.l10n_cl_dte_status, "ask_for_status")

    def test_reset_to_draft_blocked_for_accepted_and_objected(self):
        for status in ("accepted", "objected"):
            with self.subTest(status=status):
                move = self._make_invoice(status=status)
                with self.assertRaisesRegex(
                    UserError, "No se puede devolver a borrador"
                ):
                    move.button_draft()
                self.assertEqual(move.state, "posted")
                self.assertEqual(move.l10n_cl_dte_status, status)

    def test_reset_to_draft_blocked_while_asking_for_status(self):
        move = self._make_invoice(status="ask_for_status")
        with self.assertRaisesRegex(UserError, "consulta de estado"):
            move.button_draft()
        self.assertEqual(move.state, "posted")
        self.assertEqual(move.l10n_cl_dte_status, "ask_for_status")

    def test_cancel_rejected_marks_the_dte_cancelled(self):
        move = self._make_invoice(status="rejected")
        move.button_cancel()
        self.assertEqual(move.state, "cancel")
        self.assertEqual(move.l10n_cl_dte_status, "cancelled")

    def test_reset_rejected_to_draft_clears_the_dte_status(self):
        move = self._make_invoice(status="rejected")
        move.button_draft()
        self.assertEqual(move.state, "draft")
        self.assertFalse(move.l10n_cl_dte_status)

    def test_not_sent_allows_cancel_and_reset(self):
        cancelled = self._make_invoice(status="not_sent")
        cancelled.button_cancel()
        self.assertEqual(cancelled.state, "cancel")
        self.assertEqual(cancelled.l10n_cl_dte_status, "cancelled")

        reset = self._make_invoice(status="not_sent")
        reset.button_draft()
        self.assertEqual(reset.state, "draft")
        self.assertFalse(reset.l10n_cl_dte_status)

    def test_error_and_manual_allow_cancel_and_reset(self):
        for status in ("error", "manual"):
            with self.subTest(status=status):
                cancelled = self._make_invoice(status=status)
                cancelled.button_cancel()
                self.assertEqual(cancelled.state, "cancel")
                self.assertEqual(cancelled.l10n_cl_dte_status, "cancelled")

                reset = self._make_invoice(status=status)
                reset.button_draft()
                self.assertEqual(reset.state, "draft")
                self.assertFalse(reset.l10n_cl_dte_status)

    def test_cancel_move_without_document_type_is_untouched(self):
        cancelled = self._make_invoice(
            document_type_code=False, journal=self.plain_journal
        )
        self.assertFalse(cancelled.l10n_latam_document_type_id)
        cancelled.button_cancel()
        self.assertEqual(cancelled.state, "cancel")
        self.assertFalse(cancelled.l10n_cl_dte_status)

        reset = self._make_invoice(document_type_code=False, journal=self.plain_journal)
        reset.button_draft()
        self.assertEqual(reset.state, "draft")
        self.assertFalse(reset.l10n_cl_dte_status)

    def test_non_chilean_company_is_not_guarded(self):
        cancelled = self._make_invoice(status="accepted")
        reset = self._make_invoice(status="accepted")
        self.company.account_fiscal_country_id = self.env.ref("base.us")

        cancelled.button_cancel()
        self.assertEqual(cancelled.state, "cancel")
        self.assertEqual(cancelled.l10n_cl_dte_status, "accepted")

        reset.button_draft()
        self.assertEqual(reset.state, "draft")
        self.assertEqual(reset.l10n_cl_dte_status, "accepted")

    def test_rejected_dte_cannot_be_reversed(self):
        move = self._make_invoice(status="rejected")
        with self.assertRaisesRegex(UserError, "rechazado por el SII"):
            move._reverse_moves()
        self.assertEqual(move.state, "posted")
        self.assertEqual(move.l10n_cl_dte_status, "rejected")
