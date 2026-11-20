# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_dispatch.tests.common import DispatchRecordMixin
from odoo.addons.l10n_cl_dte_report.tests.common import DteReportTestCase


@tagged("post_install", "-at_install")
class TestDispatchDelivery(DispatchRecordMixin, DteReportTestCase):
    """Accepted guides reach the customer with their XML and PDF."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dispatch_values = cls.build_dispatch_values(cls.receiver)

    def _accepted_guide(self, accept=True):
        self.create_signing_profile()
        self.validate_authorization(document_code="52", folio_start=1, folio_end=10)
        dispatch = self._create_dispatch()
        self._create_line(dispatch)
        dispatch.action_review()
        dispatch.action_freeze_snapshot()
        document = dispatch.action_issue_document()
        submission = (
            self.env["l10n_cl.electronic.submission"]
            .sudo()
            .search([("document_id", "=", document.id)])
        )
        if accept:
            submission._job_send_to_sii()
            submission._job_query_sii_status()
            self.assertEqual(submission.state, "accepted")
        return dispatch, document, submission

    def _mail(self, dispatch):
        return (
            self.env["mail.mail"]
            .sudo()
            .search([("model", "=", dispatch._name), ("res_id", "=", dispatch.id)])
        )

    def test_send_emails_the_guide_xml_and_pdf(self):
        self.receiver.sudo().l10n_cl_dte_email = "dte@receiver.example"
        dispatch, document, _submission = self._accepted_guide()
        self.assertFalse(self._mail(dispatch), "Demo guides are not auto-sent")
        with self.mock_pdf_renderer():
            dispatch.with_user(self.dte_manager).action_send_dte_to_customer()
        self.assertEqual(dispatch.dte_delivery_state, "sent")
        mail = self._mail(dispatch)
        self.assertEqual(mail.email_to, "dte@receiver.example")
        prefix = f"DTE_52_{document.folio}"
        self.assertEqual(
            sorted(mail.attachment_ids.mapped("name")),
            [f"{prefix}.pdf", f"{prefix}.xml"],
        )

    def test_unaccepted_guide_cannot_be_sent(self):
        dispatch, _document, _submission = self._accepted_guide(accept=False)
        with self.assertRaises(UserError):
            dispatch.with_user(self.dte_manager).action_send_dte_to_customer()

    def test_production_acceptance_queues_the_guide_delivery(self):
        dispatch, document, submission = self._accepted_guide()
        self.env.cr.execute(
            "UPDATE l10n_cl_electronic_document SET sii_environment = 'production' "
            "WHERE id = %s",
            [document.id],
        )
        document.invalidate_recordset()
        with patch.object(type(dispatch), "with_delay") as delay:
            submission._queue_customer_delivery()
        delay.assert_called_once_with(identity_key=f"dte-deliver-{document.id}")
        delay.return_value._job_deliver_dte.assert_called_once_with(document.id)
