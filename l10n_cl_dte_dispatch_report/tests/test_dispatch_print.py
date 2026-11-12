# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import io

from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_dispatch.tests.common import DispatchRecordMixin
from odoo.addons.l10n_cl_dte_report.report.dte_document_report import (
    encode,
    render_image,
)
from odoo.addons.l10n_cl_dte_report.tests.common import DteReportTestCase


@tagged("post_install", "-at_install")
class TestDispatchPrint(DispatchRecordMixin, DteReportTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dispatch_values = cls.build_dispatch_values(cls.receiver)

    def _signed_document(self):
        self.create_signing_profile()
        self.validate_authorization(document_code="52", folio_start=1, folio_end=10)
        dispatch = self._create_dispatch()
        self._create_line(dispatch)
        dispatch.action_review()
        dispatch.action_freeze_snapshot()
        return dispatch.action_prepare_document()

    def test_signed_guide_can_have_a_sample_before_submission(self):
        # Arrange
        document = self._signed_document()
        # Act
        sample = self.create_sample(document)
        # Assert
        self.assertEqual(document.preparation_state, "signed")
        self.assertEqual(sample.document_code, "52")

    def test_sample_uses_the_dispatch_report_action(self):
        # Arrange
        sample = self.create_sample(self._signed_document())
        # Act
        action = sample._print_report_action("52")
        # Assert
        self.assertEqual(
            action, self.env.ref("l10n_cl_dte_dispatch_report.action_dispatch_print")
        )

    def test_other_codes_keep_the_default_rules(self):
        # Arrange
        sample_model = self.env["l10n_cl.dte.report.sample"]
        # Act / Assert
        self.assertEqual(sample_model._printable_states("33"), {"queued"})
        self.assertNotIn("52", sample_model._printable_states("39"))
        self.assertIn("52", sample_model._supported_document_codes())

    def test_template_renders_folio_and_totals(self):
        # Arrange
        sample = self.create_sample(self._signed_document())
        action = self.env.ref("l10n_cl_dte_dispatch_report.action_dispatch_print")
        # Act
        html, _content_type = self.env["ir.actions.report"]._render_qweb_html(
            action.id, sample.ids
        )
        # Assert
        text = html.decode()
        self.assertIn("GUÍA DE DESPACHO ELECTRÓNICA", text)
        self.assertIn("Timbre Electrónico SII", text)
        self.assertIn("2380", text)

    def test_barcode_is_encoded_from_the_literal_ted(self):
        # Arrange
        sample = self.create_sample(self._signed_document())
        report = self.env["report.l10n_cl_dte_dispatch_report.dispatch_print"]
        guide = report._get_report_values(sample.ids)["guides"][sample.id]
        image = render_image(
            encode(guide["ted"].decode("ascii"), columns=18, security_level=4),
            scale=3,
            ratio=3,
            padding=8,
        )
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=False)
        # Act / Assert
        self.assertEqual(guide["barcode"], base64.b64encode(buffer.getvalue()).decode())

    def test_generate_pdf_stores_the_sample(self):
        # Arrange
        sample = self.create_sample(self._signed_document())
        # Act
        with self.mock_pdf_renderer():
            sample.action_generate_pdf()
        # Assert
        self.assertEqual(sample.state, "generated")
