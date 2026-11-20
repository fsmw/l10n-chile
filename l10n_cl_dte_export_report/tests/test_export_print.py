# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_export.tests.common import ExportTestCase


@tagged("post_install", "-at_install")
class TestExportPrint(ExportTestCase):
    """The invoice PDF of an export shows its customs and currency data."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # SII CAF keys are 512-bit; the default 2048-bit test key makes a TED
        # too long for PDF417. This key is test-only.
        key = rsa.generate_private_key(public_exponent=65537, key_size=512)
        cls.caf_test_key = key
        cls.caf_private_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ).decode("ascii")
        cls.caf_public_pem = (
            key.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("ascii")
        )
        cls.caf_public_numbers = key.public_key().public_numbers()

    def test_issued_export_prints_customs_currency_and_stamp(self):
        self.validate_authorization(document_code="110", folio_start=60, folio_end=65)
        self.create_signing_profile()
        invoice = self._export()
        invoice.with_user(self.dte_manager).issue_dte()
        values = invoice._get_dte_export_print_values()
        self.assertEqual(values["currency"], "DOLAR USA")
        self.assertEqual(values["clp_total"], "2346167")
        self.assertIn(("Cláusula de venta", "5 - sale_clause 5"), values["rows"])
        self.assertEqual(values["references"][0]["document_type"], "807")
        html = (
            self.env["ir.actions.report"]
            ._render_qweb_html("account.report_invoice", invoice.ids)[0]
            .decode()
        )
        self.assertIn("Datos de exportación", html)
        self.assertIn("2346167", html)
        self.assertIn("Timbre Electrónico SII", html)

    def test_domestic_invoice_has_no_export_block(self):
        invoice = self.make_invoice("33")
        self.assertEqual(invoice._get_dte_export_print_values(), {})
