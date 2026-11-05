# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from decimal import Decimal
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from lxml import etree

from odoo import Command
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_boleta.services.boleta_snapshot import (
    BoletaSnapshotService,
)
from odoo.addons.l10n_cl_dte_pos.tests.test_pos_boleta_browser import (
    TestPosBoletaBrowser,
)


@tagged("post_install", "-at_install", "dte_pos_return_browser")
class TestPosReturnBrowser(TestPosBoletaBrowser):
    """Exercise synthetic signing and the native cashier workflow."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.test_tax.price_include_override = "tax_included"
        cls.whiteboard_pen.list_price = 119
        # Compact test-only CAF, as in the report suite, fits PDF417's limit.
        # The document signing certificate still uses the signing fixture.
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

    def _run_return(self, mode):
        self.company.write(
            {"dte_pos_accounting_mode": mode, "dte_pos_returns_enabled": True}
        )
        self.validate_authorization(document_code="39", folio_start=910, folio_end=930)
        self.validate_authorization(
            document_code="61", folio_start=1010, folio_end=1030
        )
        self.create_signing_profile()
        self.main_pos_config.with_user(self.pos_user).open_ui()
        with patch.object(
            BoletaSnapshotService,
            "_annual_135_uf_limit_for",
            return_value=Decimal("999999999"),
        ):
            self.start_pos_tour(
                "l10n_cl_dte_pos_return_original_sale", login="pos_user"
            )
        originals = self.main_pos_config.current_session_id.order_ids
        documents = originals.dte_document_ids | originals.account_move.dte_document_ids
        submissions = (
            self.env["l10n_cl.electronic.submission"]
            .sudo()
            .search([("document_id", "in", documents.ids)])
        )
        for submission in submissions:
            self.assertEqual(submission.environment, "demo")
            submission._job_send_to_sii()
            submission._job_query_sii_status()
        self.assertEqual(set(documents.mapped("fiscal_state")), {"accepted"})
        cash = self.main_pos_config.payment_method_ids.filtered("is_cash_count")[:1]
        journal = cash.journal_id.copy({"name": "Store B cash", "code": "RTB"})
        method = cash.copy({"name": "Store B cash", "journal_id": journal.id})
        receiver = self.main_pos_config.copy(
            {"name": "Return Store B", "payment_method_ids": [Command.set(method.ids)]}
        )
        receiver.with_user(self.pos_user).open_ui()
        self.start_pos_tour(
            "l10n_cl_dte_pos_return_cross_store", login="pos_user", pos_config=receiver
        )
        order = receiver.current_session_id.order_ids
        self.assertEqual(len(order), 1)
        self.assertLess(order.amount_total, 0)
        self.assertIn(order._get_dte_return_original_order(), originals)
        credit = order._get_pos_dte_documents()
        self.assertEqual(credit.document_code, "61")
        self.assertEqual(credit.preparation_state, "queued")
        self.assertEqual(credit.note_pos_return_order_id, order)
        self.assertEqual(order.dte_return_operation, "return")
        self.assertEqual(bool(order.account_move), mode == "per_order_invoice")
        self.assertEqual(
            len(
                credit.artifact_ids.filtered(
                    lambda artifact: artifact.role == "signed_xml"
                )
            ),
            1,
        )
        signed = credit.artifact_ids.filtered(
            lambda artifact: artifact.role == "signed_xml"
        )
        xml = etree.fromstring(base64.b64decode(signed.content))
        original_xml = etree.fromstring(
            base64.b64decode(
                order.dte_return_target_document_id.artifact_ids.filtered(
                    lambda artifact: artifact.role == "signed_xml"
                ).content
            )
        )
        original_net = Decimal(
            original_xml.xpath("string(//*[local-name()='MontoItem'])")
        )
        original_quantity = Decimal(
            original_xml.xpath("string(//*[local-name()='QtyItem'])")
        )
        self.assertEqual(
            Decimal(xml.xpath("string(//*[local-name()='PrcItem'])")),
            original_net / original_quantity,
        )
        self.assertEqual(
            order.lines.price_unit, order.lines.refunded_orderline_id.price_unit
        )
        digest = signed.sha256
        action = credit.with_user(self.dte_manager).action_create_print_sample()
        sample = (
            self.env["l10n_cl.dte.report.sample"]
            .with_user(self.dte_manager)
            .browse(action["res_id"])
        )
        sample.with_context(force_report_rendering=True).action_generate_pdf()
        self.assertTrue(base64.b64decode(sample.pdf_content).startswith(b"%PDF"))
        self.assertEqual(signed.sha256, digest)

    def test_summary_cross_store_browser(self):
        self._run_return("session_summary")

    def test_standard_cross_store_browser(self):
        self._run_return("per_order_invoice")
