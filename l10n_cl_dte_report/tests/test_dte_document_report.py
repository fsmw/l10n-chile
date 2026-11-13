# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_issuance.services.dte_issuance_service import (
    DteIssuanceService,
)

from .common import DteReportTestCase


@tagged("post_install", "-at_install")
class TestDteDocumentReport(DteReportTestCase):
    def test_print_sample_uses_the_current_signature_after_revision(self):
        invoice = self.make_invoice().with_user(self.dte_manager)
        self.validate_authorization(document_code="33", folio_start=940, folio_end=945)
        self.create_signing_profile()
        adapter = invoice._get_dte_issuance_adapter("33")
        document = DteIssuanceService().prepare(invoice, adapter, "33")
        original = document._get_signed_artifact()
        payload = base64.b64decode(original.content) + b"\n"
        current = original._create_signature_revision(original, payload, "tests/18.0")
        invoice.issue_dte()
        sample = self.create_sample(document)
        self.assertEqual(sample.signed_artifact_id, current)
        self.assertNotEqual(sample.signed_artifact_id, original)
        with self.mock_pdf_renderer():
            sample.action_generate_pdf()
        self.assertEqual(sample.state, "generated")
        self.assertTrue(original.action_verify_integrity())

    def test_generate_sample_keeps_signed_source_and_pdf_digest(self):
        document = self.create_signed_document()
        signed_artifact = document.artifact_ids.filtered(
            lambda artifact: artifact.role == "signed_xml"
        )
        sample = self.create_sample(document)

        self.assertEqual(sample.signed_artifact_id, signed_artifact)
        report_values = self.env[
            "report.l10n_cl_dte_report.dte_print_sample"
        ]._get_report_values(sample.ids)
        barcode = base64.b64decode(
            report_values["sample_data"][sample.id]["barcode"], validate=True
        )
        self.assertTrue(barcode.startswith(b"\x89PNG\r\n\x1a\n"))
        with self.mock_pdf_renderer():
            sample.action_generate_pdf()
        sample.invalidate_recordset()

        pdf_bytes = base64.b64decode(sample.pdf_content, validate=True)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertEqual(sample.state, "generated")
        self.assertEqual(sample.pdf_sha256, hashlib.sha256(pdf_bytes).hexdigest())
        self.assertEqual(sample.signed_artifact_id.sha256, signed_artifact.sha256)

    def test_report_rejects_unsupported_document_type(self):
        document = (
            self.env["l10n_cl.electronic.document"]
            .with_user(self.dte_manager)
            .create(
                {
                    "name": "Unsupported synthetic DTE",
                    "company_id": self.company.id,
                    "sii_environment": "demo",
                    "document_code": "46",
                    "source_key": "unsupported-report-sample",
                }
            )
        )
        with self.assertRaisesRegex(UserError, "not yet supported"):
            self.create_sample(document)

    def test_sample_requires_a_signed_dte(self):
        document = (
            self.env["l10n_cl.electronic.document"]
            .with_user(self.dte_manager)
            .create(
                {
                    "name": "Unsigned synthetic DTE",
                    "company_id": self.company.id,
                    "sii_environment": "demo",
                    "document_code": "33",
                    "source_key": "unsigned-report-sample",
                }
            )
        )
        with self.assertRaisesRegex(ValidationError, "prepared and signed"):
            self.create_sample(document)

    def test_generated_sample_cannot_be_changed_or_deleted(self):
        sample = self.create_sample(self.create_signed_document())
        with self.mock_pdf_renderer():
            sample.action_generate_pdf()

        with self.assertRaises(AccessError):
            sample.write({"name": "changed"})
        with self.assertRaises(AccessError):
            sample.unlink()

    def test_non_manager_cannot_create_a_sample(self):
        document = self.create_signed_document()
        with self.assertRaises(AccessError):
            self.env["l10n_cl.dte.report.sample"].with_user(self.dte_user).create(
                {"document_id": document.id}
            )
