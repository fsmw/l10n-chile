# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
import uuid

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import DteReportTestCase


@tagged("post_install", "-at_install")
class TestCertificationSamplePackage(DteReportTestCase):
    def _create_queued_synthetic_document(self, company):
        document_model = (
            self.env["l10n_cl.electronic.document"]
            .with_user(self.dte_manager)
            .with_company(company)
        )
        document = document_model.create(
            {
                "name": "Other company synthetic DTE",
                "company_id": company.id,
                "sii_environment": "demo",
                "document_code": "33",
                "source_key": uuid.uuid4().hex,
            }
        )

        def create_artifact(role, payload):
            return (
                self.env["l10n_cl.electronic.artifact"]
                .with_user(self.dte_manager)
                .with_company(company)
                .create(
                    {
                        "document_id": document.id,
                        "role": role,
                        "content": base64.b64encode(payload),
                        "mimetype": "application/xml",
                    }
                )
            )

        caf = create_artifact("caf_source", b"<CAF/>")
        document.action_record_transition(
            "authorized", "test_authorized", artifact_id=caf.id
        )
        document.action_record_transition(
            "numbered", "test_numbered", artifact_id=caf.id, folio_number=1
        )
        snapshot = create_artifact("source_snapshot", b"{}")
        document.action_record_source_snapshot(snapshot.id)
        unsigned = create_artifact("unsigned_xml", b"<DTE/>")
        document.action_record_transition(
            "prepared", "test_prepared", artifact_id=unsigned.id
        )
        signed = create_artifact("signed_xml", b"<DTE><Signature/></DTE>")
        document.action_record_transition(
            "signed", "test_signed", artifact_id=signed.id
        )
        request = create_artifact("sii_request", b"<DTE/>")
        document.action_record_transition(
            "queued", "test_queued", artifact_id=request.id
        )
        return document

    def test_package_manifest_reconciles_source_and_sample_hashes(self):
        document = self.create_signed_document()
        sample = self.create_sample(document)
        package = (
            self.env["l10n_cl.dte.report.sample.package"]
            .with_user(self.dte_manager)
            .create(
                {
                    "name": "Synthetic certification samples",
                    "company_id": self.company.id,
                    "sample_ids": [(6, 0, [sample.id])],
                }
            )
        )

        with self.mock_pdf_renderer():
            sample.action_generate_pdf()
            package.action_generate_package()
        package.invalidate_recordset()

        pdf_bytes = base64.b64decode(package.pdf_content, validate=True)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertEqual(package.pdf_sha256, hashlib.sha256(pdf_bytes).hexdigest())
        self.assertEqual(package.manifest["schema"], "l10n_cl_dte_report.package/1")
        self.assertEqual(package.manifest["samples"][0]["sample_id"], sample.id)
        self.assertEqual(
            package.manifest["samples"][0]["signed_xml_sha256"],
            sample.signed_artifact_id.sha256,
        )
        self.assertEqual(
            package.manifest["samples"][0]["pdf_sha256"], sample.pdf_sha256
        )

    def test_package_rejects_samples_from_another_company(self):
        document = self.create_signed_document()
        sample = self.create_sample(document)
        other_company = (
            self.env["res.company"].sudo().create({"name": "Other synthetic company"})
        )
        self.dte_manager.sudo().write({"company_ids": [(4, other_company.id)]})
        other_document = self._create_queued_synthetic_document(other_company)
        other_sample = (
            self.env["l10n_cl.dte.report.sample"]
            .with_user(self.dte_manager)
            .with_company(other_company)
            .create({"document_id": other_document.id})
        )

        with self.assertRaisesRegex(ValidationError, "belong to the package company"):
            self.env["l10n_cl.dte.report.sample.package"].with_user(
                self.dte_manager
            ).create(
                {
                    "name": "Invalid package",
                    "company_id": self.company.id,
                    "sample_ids": [(6, 0, [sample.id, other_sample.id])],
                }
            )
