# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
import io
from unittest.mock import patch

from pypdf import PdfReader

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, tagged

from odoo.addons.l10n_cl_dte_note.tests.common import DteNoteTestCase

from ..models.report_sample import _extract_ted_bytes
from ..report.dte_document_report import encode
from .common import DteReportTestCase


@tagged("post_install", "-at_install")
class TestNoteReport(DteReportTestCase, DteNoteTestCase, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The native debit wizard recomputes LATAM types from fiscal country
        # and journal policy, not the manually selected type in generic fixtures.
        cls.company.sudo().account_fiscal_country_id = cls.chile
        taxes = cls.tax_19 | cls.tax_sale_a | cls.tax_purchase_a
        taxes.tax_group_id.sudo().country_id = cls.chile
        taxes.sudo().country_id = cls.chile
        cls.company.partner_id.sudo().l10n_cl_sii_taxpayer_type = "1"
        cls.receiver.l10n_cl_sii_taxpayer_type = "1"
        cls.company_data["default_journal_sale"].l10n_latam_use_documents = True

    def _issue_note(self, source, target, code, operation, reason):
        if code == "61":
            note = self.create_credit_note(source, target, operation=operation)
            note.dte_note_reason = reason
            line = note.invoice_line_ids.filtered(
                lambda item: item.display_type == "product"
            )[:1]
            if operation == "return":
                line.quantity /= 2
            elif operation == "amount":
                line.price_unit /= 2
        else:
            wizard = self.env["account.debit.note"].create(
                {
                    "move_ids": [Command.set(source.ids)],
                    "date": source.invoice_date,
                    "reason": reason,
                    "dte_note_operation": operation,
                }
            )
            action = wizard.create_debit()
            note = self.env["account.move"].browse(action["res_id"])
            if operation == "amount":
                source_line = source.invoice_line_ids.filtered(
                    lambda item: item.display_type == "product"
                )[:1]
                note.invoice_line_ids = [
                    Command.create(
                        {
                            "product_id": source_line.product_id.id,
                            "account_id": source_line.account_id.id,
                            "name": "Synthetic debit adjustment",
                            "quantity": 1,
                            "price_unit": 100,
                            "dte_note_origin_line_number": 1,
                            "tax_ids": [Command.set(source_line.tax_ids.ids)],
                        }
                    )
                ]
        self.validate_authorization(document_code=code, folio_start=920, folio_end=923)
        note.action_post()
        document = note.with_user(self.dte_manager).issue_dte()
        return self.create_sample(document), note

    def _create_note_sample(self, code="61", operation="cancel", reason=None):
        source, target, _snapshot = self.create_accepted_source()
        self.create_signing_profile()
        return self._issue_note(
            source, target, code, operation, reason or "Anulación documentada"
        )

    def _check_pdf(self, sample, note, label, operation_label):
        sample = sample.with_context(force_report_rendering=True)
        artifacts = sample.document_id.artifact_ids
        original = {item.id: (item.sha256, item.content) for item in artifacts}
        signed_xml = sample._get_signed_xml()
        with patch(
            "odoo.addons.l10n_cl_dte_report.report.dte_document_report.encode",
            wraps=encode,
        ) as barcode_encoder:
            values = self.env[
                "report.l10n_cl_dte_report.dte_print_sample"
            ]._get_report_values(sample.ids)["sample_data"][sample.id]
            barcode_encoder.assert_called_once_with(
                _extract_ted_bytes(signed_xml).decode("ascii"),
                columns=18,
                security_level=4,
            )
        reference = values["references"][0]
        self.assertEqual(
            reference["folio"], str(note.dte_note_target_document_id.folio)
        )
        self.assertEqual(
            reference["document_code"], note.dte_note_target_document_id.document_code
        )
        self.assertEqual(reference["reason"], note.dte_note_reason)
        self.assertEqual(
            reference["date"], str(note.dte_note_target_move_id.invoice_date)
        )
        self.assertEqual(
            reference["code"], "1" if note.dte_note_operation == "cancel" else "3"
        )
        self.assertEqual(values["total"], str(int(note.amount_total)))
        sample.action_generate_pdf()
        pdf_bytes = base64.b64decode(sample.pdf_content)
        pdf = PdfReader(io.BytesIO(pdf_bytes))
        self.assertEqual(len(pdf.pages), 1)
        self.assertAlmostEqual(
            float(pdf.pages[0].mediabox.width) * 25.4 / 72, 210, delta=1
        )
        text = pdf.pages[0].extract_text()
        self.assertNotIn(note.name, text)
        for expected in (
            label,
            operation_label,
            "Referencias",
            "Folio",
            reference["folio"],
            reference["date"],
            reference["reason"],
            values["total"],
            "IVA",
        ):
            self.assertIn(expected, text)
        self.assertEqual(sample.pdf_sha256, hashlib.sha256(pdf_bytes).hexdigest())
        artifacts.invalidate_recordset()
        self.assertEqual(
            {item.id: (item.sha256, item.content) for item in artifacts}, original
        )
        self.assertEqual(sample._get_signed_xml(), signed_xml)
        self.assertIn(sample.pdf_filename, sample.action_download_pdf()["url"])
        with self.assertRaises(AccessError):
            sample.write({"name": "Changed note sample"})
        with self.assertRaises(AccessError):
            sample.unlink()
        with self.assertRaisesRegex(UserError, "already been generated"):
            sample.action_generate_pdf()
        return text

    def test_credit_cancellation_a4(self):
        sample, note = self._create_note_sample()
        self._check_pdf(sample, note, "Nota de crédito electrónica", "Anulación")

    def test_partial_return_a4(self):
        sample, note = self._create_note_sample(
            operation="return", reason="Devolución parcial"
        )
        self._check_pdf(
            sample, note, "Nota de crédito electrónica", "Corrección de montos"
        )

    def test_partial_amount_reduction_a4(self):
        sample, note = self._create_note_sample(
            operation="amount", reason="Rebaja parcial"
        )
        self._check_pdf(
            sample, note, "Nota de crédito electrónica", "Corrección de montos"
        )

    def test_debit_amount_increase_a4(self):
        sample, note = self._create_note_sample("56", "amount", "Aumento de monto")
        self._check_pdf(
            sample, note, "Nota de débito electrónica", "Corrección de montos"
        )

    def test_debit_cancellation_of_credit_a4(self):
        credit_sample, credit = self._create_note_sample()
        target = credit_sample.document_id
        for state, role in (
            ("submitted", "sii_response"),
            ("accepted", "status_response"),
        ):
            artifact = self.create_artifact(target, role, b"synthetic local acceptance")
            target.action_record_transition(
                state, "test_accepted_credit", artifact_id=artifact.id
            )
        sample, note = self._issue_note(
            credit, target, "56", "cancel", "Anula nota de crédito"
        )
        self._check_pdf(sample, note, "Nota de débito electrónica", "Anulación")

    def test_note_report_requires_manager(self):
        sample, _note = self._create_note_sample()
        with self.assertRaises(AccessError):
            self.env[sample._name].with_user(self.dte_user).create(
                {"document_id": sample.document_id.id}
            )
        other_user_sample = sample.with_user(self.dte_user)
        with self.assertRaises(AccessError):
            other_user_sample.action_generate_pdf()
        with self.assertRaises(AccessError):
            other_user_sample.action_download_pdf()
        with self.assertRaises(AccessError):
            self.env["report.l10n_cl_dte_report.dte_print_sample"].with_user(
                self.dte_user
            )._get_report_values(sample.ids)

    def test_note_is_not_printed_as_thermal_receipt(self):
        sample, _note = self._create_note_sample()
        with self.assertRaisesRegex(UserError, "only boletas"):
            sample.action_print_thermal()
        with self.assertRaisesRegex(ValidationError, "only boletas"):
            self.env["report.l10n_cl_dte_report.boleta_receipt"]._get_report_values(
                sample.ids
            )

    def test_tampered_note_is_rejected_even_with_valid_artifact_hash(self):
        sample, _note = self._create_note_sample()
        content = sample._get_signed_xml().replace(
            b"<FolioRef>730</FolioRef>", b"<FolioRef>731</FolioRef>"
        )
        self.assertNotEqual(content, sample._get_signed_xml())
        original_create = self.create_artifact

        def create_evidence(document, role, payload=b"synthetic evidence"):
            return original_create(
                document, role, content if role == "signed_xml" else payload
            )

        other = self.create_document(document_code="61")
        with patch.object(self, "create_artifact", side_effect=create_evidence):
            self.put_document_in_queued_state(other)
        other_sample = self.create_sample(other)
        other_sample.signed_artifact_id.action_verify_integrity()
        with self.assertRaisesRegex(ValidationError, "digest"):
            other_sample.action_generate_pdf()
        self.assertEqual(other_sample.state, "draft")
        self.assertFalse(other_sample.pdf_content)
