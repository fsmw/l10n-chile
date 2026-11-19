# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_report.tests.common import DteReportTestCase


@tagged("post_install", "-at_install")
class TestDteDelivery(DteReportTestCase):
    """Accepted DTEs reach the customer with their XML and stamped PDF."""

    def _issue_and_accept(self, folio_start=750):
        document = self.create_signed_document(folio_start=folio_start)
        submission = (
            self.env["l10n_cl.electronic.submission"]
            .search([("document_id", "=", document.id)])
            .with_context(queue_job__no_delay=True)
        )
        submission._job_send_to_sii()
        submission._job_query_sii_status()
        self.assertEqual(submission.state, "accepted")
        return document, document.source_move_id

    def _sent_mail(self, move):
        return (
            self.env["mail.mail"]
            .sudo()
            .search([("model", "=", "account.move"), ("res_id", "=", move.id)])
        )

    def _send(self, move):
        with self.mock_pdf_renderer():
            move.with_user(self.dte_manager).action_send_dte_to_customer()

    def test_demo_acceptance_is_not_emailed_to_the_customer(self):
        self.receiver.sudo().l10n_cl_dte_email = "dte@receiver.example"
        _document, move = self._issue_and_accept()
        self.assertEqual(move.dte_delivery_state, "not_sent")
        self.assertFalse(self._sent_mail(move))

    def test_production_acceptance_queues_the_delivery_of_that_dte(self):
        document = self.create_signed_document(folio_start=780)
        submission = (
            self.env["l10n_cl.electronic.submission"]
            .sudo()
            .search([("document_id", "=", document.id)])
        )
        submission._job_send_to_sii()
        submission._job_query_sii_status()
        self.assertEqual(submission.state, "accepted")
        # Only the DTE mode matters for the delivery rule.
        self.env.cr.execute(
            "UPDATE l10n_cl_electronic_document SET sii_environment = 'production' "
            "WHERE id = %s",
            [document.id],
        )
        document.invalidate_recordset()
        move_model = type(self.env["account.move"])
        with patch.object(move_model, "with_delay") as delay:
            submission._queue_customer_delivery()
        delay.assert_called_once_with(identity_key=f"dte-deliver-{document.id}")
        delay.return_value._job_deliver_dte.assert_called_once_with(document.id)

    def test_delivery_emails_the_xml_and_the_stamped_pdf(self):
        self.receiver.sudo().l10n_cl_dte_email = "dte@receiver.example"
        document, move = self._issue_and_accept()
        self._send(move)
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
        document, move = self._issue_and_accept(folio_start=760)
        move._job_deliver_dte(document.id)
        self.assertEqual(move.dte_delivery_state, "failed")
        self.assertIn("email", move.dte_delivery_message)
        self.assertFalse(self._sent_mail(move))
        with self.assertRaises(UserError):
            self._send(move)

    def test_render_failure_is_reported_on_the_invoice(self):
        self.receiver.sudo().l10n_cl_dte_email = "dte@receiver.example"
        document, move = self._issue_and_accept(folio_start=790)
        move_model = type(self.env["account.move"])
        with patch.object(
            move_model, "_dte_delivery_attachments", side_effect=ValueError("bad")
        ):
            move._job_deliver_dte(document.id)
        self.assertEqual(move.dte_delivery_state, "failed")
        self.assertEqual(move.dte_delivery_message, "bad")

    def test_unaccepted_dte_cannot_be_delivered(self):
        document = self.create_signed_document(folio_start=770)
        with self.assertRaises(UserError):
            self._send(document.source_move_id)
