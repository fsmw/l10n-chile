# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import TransactionCase, tagged

EXISTING_CODES = {"33", "34", "39", "41", "61", "56"}


@tagged("post_install", "-at_install")
class TestReportSampleHooks(TransactionCase):
    def setUp(self):
        super().setUp()
        self.sample_model = self.env["l10n_cl.dte.report.sample"]

    def test_supported_codes_include_the_existing_ones(self):
        # Arrange / Act
        codes = self.sample_model._supported_document_codes()
        # Assert: other addons may add codes, but none of these may be lost
        self.assertTrue(EXISTING_CODES <= codes)

    def test_only_queued_documents_can_be_printed_by_default(self):
        # Arrange / Act / Assert
        for code in EXISTING_CODES:
            self.assertEqual(self.sample_model._printable_states(code), {"queued"})

    def test_default_report_action_is_the_print_sample(self):
        # Arrange
        expected = self.env.ref("l10n_cl_dte_report.action_dte_print_sample")
        # Act / Assert
        self.assertEqual(self.sample_model._print_report_action("33"), expected)
