# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import io
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pypdf import PdfWriter

from odoo.addons.base.models.ir_actions_report import IrActionsReport
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase


class DteReportTestCase(DteInvoiceTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Keep the synthetic CAF compact enough for PDF417's symbol limit.
        # This key is test-only and is never used for SII or production data.
        cls.caf_test_key = rsa.generate_private_key(public_exponent=65537, key_size=512)
        cls.caf_private_pem = cls.caf_test_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ).decode("ascii")
        cls.caf_public_pem = (
            cls.caf_test_key.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("ascii")
        )
        cls.caf_public_numbers = cls.caf_test_key.public_key().public_numbers()

    def mock_pdf_renderer(self):
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        output = io.BytesIO()
        writer.write(output)
        return patch.object(
            IrActionsReport,
            "_render_qweb_pdf",
            return_value=(output.getvalue(), "pdf"),
        )

    def create_signed_document(self, code="33", folio_start=710):
        invoice = self.make_invoice(code=code)
        self.validate_authorization(
            document_code=code,
            folio_start=folio_start,
            folio_end=folio_start + 3,
        )
        self.create_signing_profile()
        return invoice.with_user(self.dte_manager).issue_dte()

    def create_sample(self, document):
        return (
            self.env["l10n_cl.dte.report.sample"]
            .with_user(self.dte_manager)
            .create({"document_id": document.id})
        )
