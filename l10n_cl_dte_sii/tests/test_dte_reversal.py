# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from ..services.sii_client import SiiTransportError


@tagged("post_install", "-at_install")
class TestDteReversalDocumentType(AccountTestInvoicingCommon):
    @classmethod
    @AccountTestInvoicingCommon.setup_country("cl")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.env.user.groups_id = [
            (
                4,
                cls.env.ref("l10n_cl_dte_sii.group_dte_user").id,
            )
        ]
        cls.partner = cls.partner_a
        cls.partner.write(
            {
                "country_id": cls.company.country_id.id,
                "vat": "76543210-3",
                "l10n_cl_sii_taxpayer_type": "1",
            }
        )
        cls.journal = cls.company_data["default_journal_sale"]
        cls.journal.write({"l10n_latam_use_documents": True})

    def _make_source_move(self, source_code):
        document_type = self.env["l10n_latam.document.type"].search(
            [("country_id.code", "=", "CL"), ("code", "=", source_code)], limit=1
        )
        self.assertTrue(document_type, f"Missing Chilean DTE type {source_code}")
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "journal_id": self.journal.id,
                "partner_id": self.partner.id,
                "invoice_date": "2026-10-28",
                "l10n_latam_document_type_id": document_type.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Servicio de reversa",
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
        return move

    def _available_reversal_codes(self, source_code):
        move = self._make_source_move(source_code)
        wizard = self.env["account.move.reversal"].create(
            {
                "move_ids": [(6, 0, move.ids)],
                "journal_id": self.journal.id,
            }
        )
        wizard._compute_documents_info()
        return wizard.l10n_latam_available_document_type_ids.mapped("code")

    def test_domestic_invoice_reversals_select_domestic_credit_note(self):
        for source_code in ("33", "34"):
            with self.subTest(source_code=source_code):
                self.assertEqual(self._available_reversal_codes(source_code), ["61"])

    def test_export_invoice_reversal_selects_export_credit_note(self):
        self.assertEqual(self._available_reversal_codes("110"), ["112"])

    def test_reversal_creates_credit_note_with_source_reference(self):
        source = self._make_source_move("33")
        wizard = (
            self.env["account.move.reversal"]
            .with_context(active_model="account.move", active_ids=source.ids)
            .create(
                {
                    "reason": "Anulación por error de monto",
                    "journal_id": self.journal.id,
                }
            )
        )

        action = wizard.refund_moves()
        credit_note = self.env["account.move"].browse(action["res_id"])

        self.assertEqual(credit_note.state, "draft")
        self.assertEqual(credit_note.l10n_latam_document_type_id.code, "61")
        self.assertEqual(len(credit_note.l10n_cl_reference_ids), 1)
        reference = credit_note.l10n_cl_reference_ids
        self.assertEqual(
            reference.l10n_cl_reference_doc_type_id, source.l10n_latam_document_type_id
        )
        self.assertEqual(reference.origin_doc_number, source.l10n_latam_document_number)
        self.assertEqual(reference.reference_doc_code, "3")
        self.assertEqual(reference.reason, wizard.reason)
        self.assertEqual(reference.date, source.invoice_date)

    def test_text_correction_reversal_has_zero_amount_and_reference(self):
        source = self._make_source_move("33")
        wizard = (
            self.env["account.move.reversal"]
            .with_context(active_model="account.move", active_ids=source.ids)
            .create(
                {
                    "reason": "Corrección de dirección",
                    "l10n_cl_is_text_correction": True,
                    "l10n_cl_original_text": "Dirección incorrecta",
                    "l10n_cl_corrected_text": "Dirección correcta",
                    "journal_id": self.journal.id,
                }
            )
        )

        action = wizard.refund_moves()
        credit_note = self.env["account.move"].browse(action["res_id"])

        self.assertEqual(credit_note.l10n_cl_reference_ids.reference_doc_code, "2")
        self.assertEqual(credit_note.amount_total, 0)
        self.assertIn("Dirección incorrecta", credit_note.invoice_line_ids.name)
        self.assertIn("Dirección correcta", credit_note.invoice_line_ids.name)

    def test_debit_note_wizard_creates_type_56_with_source_reference(self):
        source = self._make_source_move("33")
        wizard = (
            self.env["account.debit.note"]
            .with_context(active_model="account.move", active_ids=source.ids)
            .create(
                {
                    "reason": "Ajuste de monto",
                    "l10n_cl_edi_reference_doc_code": "3",
                }
            )
        )

        wizard.create_debit()
        debit_note = self.env["account.move"].search(
            [("debit_origin_id", "=", source.id)], limit=1
        )

        self.assertTrue(debit_note)
        self.assertEqual(debit_note.l10n_latam_document_type_id.code, "56")
        self.assertEqual(
            debit_note.l10n_cl_reference_ids.origin_doc_number,
            source.l10n_latam_document_number,
        )
        self.assertEqual(debit_note.l10n_cl_reference_ids.reference_doc_code, "3")
        self.assertEqual(debit_note.l10n_cl_reference_ids.reason, wizard.reason)

    def test_debit_note_text_correction_has_zero_amount(self):
        source = self._make_source_move("33")
        wizard = (
            self.env["account.debit.note"]
            .with_context(active_model="account.move", active_ids=source.ids)
            .create(
                {
                    "reason": "Corrección de glosa",
                    "l10n_cl_edi_reference_doc_code": "2",
                    "l10n_cl_original_text": "Glosa incorrecta",
                    "l10n_cl_corrected_text": "Glosa correcta",
                }
            )
        )

        wizard.create_debit()
        debit_note = self.env["account.move"].search(
            [("debit_origin_id", "=", source.id)], limit=1
        )

        self.assertEqual(debit_note.amount_total, 0)
        self.assertEqual(debit_note.l10n_cl_reference_ids.reference_doc_code, "2")
        self.assertIn("Glosa incorrecta", debit_note.invoice_line_ids.name)
        self.assertIn("Glosa correcta", debit_note.invoice_line_ids.name)

    def test_rejected_dte_must_be_corrected_instead_of_reversed(self):
        source = self._make_source_move("33")
        source._l10n_cl_write_dte_lifecycle_values(
            {"l10n_cl_dte_status": "rejected"}
        )
        wizard = (
            self.env["account.move.reversal"]
            .with_context(active_model="account.move", active_ids=source.ids)
            .create({"reason": "Documento rechazado", "journal_id": self.journal.id})
        )

        with self.assertRaises(UserError):
            wizard.refund_moves()

    def test_rejected_dte_cannot_be_reversed_through_model_api(self):
        source = self._make_source_move("33")
        source._l10n_cl_write_dte_lifecycle_values(
            {"l10n_cl_dte_status": "rejected"}
        )

        with self.assertRaisesRegex(UserError, "rechazado por el SII"):
            source._reverse_moves()

    def test_rejected_dte_must_be_corrected_instead_of_debited(self):
        source = self._make_source_move("33")
        source._l10n_cl_write_dte_lifecycle_values(
            {"l10n_cl_dte_status": "rejected"}
        )
        wizard = (
            self.env["account.debit.note"]
            .with_context(active_model="account.move", active_ids=source.ids)
            .create({"reason": "Documento rechazado"})
        )
        before = self.env["account.move"].search_count(
            [("debit_origin_id", "=", source.id)]
        )

        with self.assertRaisesRegex(UserError, "rechazado por el SII"):
            wizard.create_debit()

        self.assertEqual(
            self.env["account.move"].search_count(
                [("debit_origin_id", "=", source.id)]
            ),
            before,
        )

    def test_credit_note_timeout_can_be_resolved_as_rejected_without_retry(self):
        self.company.sudo().l10n_cl_dte_environment = "SIITEST"
        source = self._make_source_move("33")
        source._l10n_cl_write_dte_lifecycle_values(
            {"l10n_cl_dte_status": "accepted"}
        )
        wizard = (
            self.env["account.move.reversal"]
            .with_context(active_model="account.move", active_ids=source.ids)
            .create({"reason": "Ajuste de monto", "journal_id": self.journal.id})
        )
        action = wizard.reverse_moves()
        credit_note = self.env["account.move"].browse(action["res_id"])
        credit_note.with_context(l10n_cl_skip_dte_emission=True).action_post()
        envelope = self.env["ir.attachment"].create(
            {
                "name": "credit-note-envelope.xml",
                "raw": b"<EnvioDTE/>",
                "mimetype": "application/xml",
                "res_model": "account.move",
                "res_id": credit_note.id,
                "company_id": self.company.id,
            }
        )
        credit_note._l10n_cl_write_dte_lifecycle_values(
            {
                "l10n_cl_dte_folio": 101,
                "l10n_cl_dte_envelope_attachment_id": envelope.id,
                "l10n_cl_dte_status": "not_sent",
            }
        )

        with (
            patch.object(
                type(self.company), "_get_digital_signature", return_value=object()
            ),
            patch(
                "odoo.addons.l10n_cl_dte_sii.models.account_move.SiiDteClient"
            ) as client_class,
        ):
            client_class.return_value.send.side_effect = SiiTransportError(
                "SOAP timeout after dispatch",
                outcome_unknown=True,
                track_id="1234567890",
            )
            credit_note.action_l10n_cl_dte_send()

            self.assertEqual(credit_note.l10n_cl_dte_status, "error")
            self.assertEqual(credit_note.l10n_cl_dte_track_id, "1234567890")
            self.assertEqual(len(credit_note.l10n_cl_reference_ids), 1)
            with self.assertRaises(UserError):
                credit_note.action_l10n_cl_dte_send()
            self.assertEqual(client_class.return_value.send.call_count, 1)

            client_class.return_value.check_status.return_value = {
                "status": "rejected",
                "diagnostic": "Documento rechazado por el SII",
            }
            credit_note.action_l10n_cl_dte_check_status()

            self.assertEqual(credit_note.l10n_cl_dte_status, "rejected")
            self.assertTrue(credit_note.l10n_cl_dte_last_status_check)
            self.assertEqual(client_class.return_value.send.call_count, 1)
            with self.assertRaises(UserError):
                credit_note.action_l10n_cl_dte_send()

        debit_wizard = (
            self.env["account.debit.note"]
            .with_context(active_model="account.move", active_ids=credit_note.ids)
            .create({"reason": "No corregir un DTE rechazado"})
        )
        with self.assertRaisesRegex(UserError, "rechazado por el SII"):
            debit_wizard.create_debit()

    def test_unmapped_source_type_keeps_latin_american_default(self):
        codes = self._available_reversal_codes("39")
        self.assertTrue(codes)
