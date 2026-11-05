# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import io
from decimal import Decimal
from unittest.mock import patch

from pypdf import PdfReader

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, tagged

from odoo.addons.l10n_cl_dte_boleta.services.boleta_snapshot import (
    BoletaSnapshotService,
)

from .common import DteReportTestCase


@tagged("post_install", "-at_install")
class TestBoletaReceipt(DteReportTestCase, HttpCase):
    def _create_boleta_sample(self, code, line_count=1):
        invoice = self.make_invoice(code=code, taxable=code == "39", post=False)
        line = invoice.invoice_line_ids[0]
        invoice.write(
            {
                "invoice_line_ids": [
                    Command.create(
                        {
                            "product_id": line.product_id.id,
                            "account_id": line.account_id.id,
                            "name": f"Receipt line {number}",
                            "quantity": 1,
                            "price_unit": 100,
                            "tax_ids": [Command.set(line.tax_ids.ids)],
                        }
                    )
                    for number in range(2, line_count + 1)
                ]
            }
        )
        invoice.action_post()
        self.validate_authorization(document_code=code, folio_start=810, folio_end=813)
        self.create_signing_profile()
        with patch.object(
            BoletaSnapshotService,
            "_annual_135_uf_limit_for",
            return_value=Decimal("999999999"),
        ):
            document = invoice.with_user(self.dte_manager).issue_dte()
        return self.create_sample(document)

    def _check_real_boleta_reports(self, code):
        sample = self._create_boleta_sample(code).with_context(
            force_report_rendering=True
        )
        artifact = sample.signed_artifact_id
        original = base64.b64decode(artifact.content)
        values = self.env[
            "report.l10n_cl_dte_report.boleta_receipt"
        ]._get_report_values(sample.ids)["sample_data"][sample.id]
        self.assertEqual(values["issuer_name"], self.company.name)
        self.assertEqual(values["document_code"], code)
        self.assertTrue(values["exempt"] if code == "41" else values["vat"])
        sample.action_generate_pdf()
        a4 = PdfReader(io.BytesIO(base64.b64decode(sample.pdf_content)))
        self.assertEqual(len(a4.pages), 1)
        self.assertAlmostEqual(
            float(a4.pages[0].mediabox.width) * 25.4 / 72, 210, delta=1
        )
        a4_hash = sample.pdf_sha256
        action = sample.action_print_thermal()
        pdf, _kind = (
            self.env["ir.actions.report"]
            .with_context(force_report_rendering=True)
            ._render_qweb_pdf(action["report_name"], res_ids=sample.ids)
        )
        receipt = PdfReader(io.BytesIO(pdf))
        self.assertTrue(receipt.pages)
        self.assertAlmostEqual(
            float(receipt.pages[0].mediabox.width) * 25.4 / 72, 80, delta=1
        )
        text = " ".join(page.extract_text() for page in receipt.pages)
        self.assertIn("Total CLP", text)
        self.assertIn(values["total"], text)
        self.assertIn("exenta" if code == "41" else "electrónica", text)
        self.assertEqual(sample.pdf_sha256, a4_hash)
        self.assertEqual(base64.b64decode(artifact.content), original)

    def test_taxable_boleta_a4_and_thermal_pdf(self):
        self._check_real_boleta_reports("39")

    def test_exempt_boleta_a4_and_thermal_pdf(self):
        self._check_real_boleta_reports("41")

    def test_thermal_rejects_invoice_and_non_manager(self):
        sample = self.create_sample(self.create_signed_document())
        with self.assertRaisesRegex(UserError, "only boletas"):
            sample.action_print_thermal()
        with self.assertRaises(AccessError):
            sample.with_user(self.dte_user).action_print_thermal()

    def test_thermal_report_rejects_invoice_through_report_route(self):
        sample = self.create_sample(self.create_signed_document())
        with self.assertRaisesRegex(ValidationError, "only boletas"):
            self.env["report.l10n_cl_dte_report.boleta_receipt"]._get_report_values(
                sample.ids
            )

    def test_invoice_still_renders_one_a4_page(self):
        sample = self.create_sample(self.create_signed_document()).with_context(
            force_report_rendering=True
        )
        sample.action_generate_pdf()
        pdf = PdfReader(io.BytesIO(base64.b64decode(sample.pdf_content)))
        self.assertEqual(len(pdf.pages), 1)
        self.assertIn(self.company.name, pdf.pages[0].extract_text())

    def test_long_thermal_receipt_paginates_without_losing_details(self):
        sample = self._create_boleta_sample("39", line_count=80)
        pdf, _kind = (
            self.env["ir.actions.report"]
            .with_context(force_report_rendering=True)
            ._render_qweb_pdf(
                sample.action_print_thermal()["report_name"], res_ids=sample.ids
            )
        )
        receipt = PdfReader(io.BytesIO(pdf))
        self.assertGreater(len(receipt.pages), 1)
        self.assertIn(
            "Receipt line 80", " ".join(page.extract_text() for page in receipt.pages)
        )
        for page in receipt.pages:
            self.assertAlmostEqual(float(page.mediabox.width) * 25.4 / 72, 80, delta=1)

    def test_valid_xml_for_another_folio_is_rejected(self):
        sample = self._create_boleta_sample("39")
        content = base64.b64decode(sample.signed_artifact_id.content)
        original_create = self.create_artifact

        def create_evidence(document, role, payload=b"synthetic evidence"):
            return original_create(
                document, role, content if role == "signed_xml" else payload
            )

        other = self.create_document(document_code="39")
        with patch.object(self, "create_artifact", side_effect=create_evidence):
            self.put_document_in_queued_state(other)
        other_sample = self.create_sample(other)
        with self.assertRaisesRegex(ValidationError, "identity does not match"):
            other_sample._get_signed_xml()
