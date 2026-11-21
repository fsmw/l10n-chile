# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_gateway.tests.common_certification_request import (
    FakeSiiSession,
    configure_company_certification,
    create_sii_caf,
    dte_status_response,
    fake_sii,
)
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase


@tagged("post_install", "-at_install")
class TestCertificationIssuance(DteInvoiceTestCase):
    """Accounting issues 33/34 to SII Certification like it does to Production."""

    def setUp(self):
        super().setUp()
        configure_company_certification(self)

    def _issue(self, code, folio):
        create_sii_caf(self, "certification", code=code, folio_start=folio)
        invoice = self.make_invoice(code=code, taxable=code == "33")
        move = self.env["account.move"].with_user(self.dte_manager).browse(invoice.id)
        document = move.issue_dte()
        submission = (
            self.env["l10n_cl.electronic.submission"]
            .sudo()
            .search([("document_id", "=", document.id)])
        )
        return move, document, submission

    def _check_issue_send_and_query(self, code, folio):
        move, document, submission = self._issue(code, folio)
        self.assertEqual(document.sii_environment, "certification")
        self.assertEqual(submission.environment, "certification")
        session = FakeSiiSession(statuses=[dte_status_response(code)])
        with fake_sii(session):
            submission._job_send_to_sii()
            envelope = submission.transport_request_artifact_id
            self.assertEqual(submission.state, "submitted", submission.sii_message)
            # Repeating the Accounting action keeps the DTE, folio and envelope.
            self.assertEqual(move.issue_dte(), document)
            submission.with_context(
                queue_job__no_delay=True
            ).action_enqueue_status_query()
        self.assertEqual(session.upload_count(), 1)
        self.assertEqual(submission.transport_request_artifact_id, envelope)
        self.assertEqual(document.folio, folio)
        self.assertEqual(len(move.dte_document_ids), 1)
        self.assertEqual(submission.state, "accepted", submission.sii_message)
        self.assertEqual(document.fiscal_state, "accepted")

    def test_invoice_33_issued_sent_and_queried_in_certification(self):
        self._check_issue_send_and_query("33", 800)

    def test_invoice_34_issued_sent_and_queried_in_certification(self):
        self._check_issue_send_and_query("34", 900)
