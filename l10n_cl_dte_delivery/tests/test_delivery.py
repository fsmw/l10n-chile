# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_report.tests.common import DteReportTestCase


@tagged("post_install", "-at_install")
class TestDteDelivery(DteReportTestCase):
    """SII acceptance triggers one customer email with the XML and the PDF."""

    def _issue_and_accept(self, folio_start=750):
        document = self.create_signed_document(folio_start=folio_start)
        submission = (
            self.env["l10n_cl.electronic.submission"]
            .search([("document_id", "=", document.id)])
            .with_context(queue_job__no_delay=True)
        )
        submission._job_send_to_sii()
        with self.mock_pdf_renderer():
            submission._job_query_sii_status()
        self.assertEqual(submission.state, "accepted")
        return document, document.source_move_id

    def _sent_mail(self, move):
        return (
            self.env["mail.mail"]
            .sudo()
            .search([("model", "=", "account.move"), ("res_id", "=", move.id)])
        )

    def test_acceptance_emails_the_xml_and_the_stamped_pdf(self):
        self.receiver.sudo().l10n_cl_dte_email = "dte@receiver.example"
        document, move = self._issue_and_accept()
        self.assertEqual(move.dte_delivery_state, "sent")
        mail = self._sent_mail(move)
        self.assertEqual(len(mail), 1)
        self.assertEqual(mail.email_to, "dte@receiver.example")
        names = sorted(mail.attachment_ids.mapped("name"))
        prefix = f"DTE_{document.document_code}_{document.folio}"
        self.assertEqual(names, [f"{prefix}.pdf", f"{prefix}.xml"])
        xml = mail.attachment_ids.filtered(lambda item: item.name.endswith(".xml"))
        self.assertIn(b"<TED", xml.raw)

    def test_missing_email_is_reported_on_the_invoice(self):
        self.receiver.sudo().write({"email": False, "l10n_cl_dte_email": False})
        _document, move = self._issue_and_accept(folio_start=760)
        self.assertEqual(move.dte_delivery_state, "failed")
        self.assertIn("email", move.dte_delivery_message)
        self.assertFalse(self._sent_mail(move))
        with self.assertRaises(UserError):
            move.with_user(self.dte_manager).action_send_dte_to_customer()

    def test_unaccepted_dte_cannot_be_delivered(self):
        document = self.create_signed_document(folio_start=770)
        with self.assertRaises(UserError):
            document.source_move_id.with_user(
                self.dte_manager
            ).action_send_dte_to_customer()
