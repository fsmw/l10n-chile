# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase


@tagged("post_install", "-at_install")
class TestIssuedMoveGuard(DteInvoiceTestCase):
    """An issued DTE freezes what it states about the invoice."""

    def _issue(self, folio_start, scenario="accepted"):
        invoice = self.make_invoice()
        self.validate_authorization(
            document_code="33", folio_start=folio_start, folio_end=folio_start + 3
        )
        self.create_signing_profile()
        document = invoice.with_user(self.dte_manager).issue_dte()
        submission = (
            self.env["l10n_cl.electronic.submission"]
            .with_user(self.dte_manager)
            .search([("document_id", "=", document.id)])
        )
        if scenario != "accepted":
            submission.demo_scenario = scenario
        submission._job_send_to_sii()
        submission._job_query_sii_status()
        return invoice, document

    def test_issued_invoice_cannot_be_reset_cancelled_edited_or_deleted(self):
        invoice, _document = self._issue(600)
        for action in (invoice.button_draft, invoice.button_cancel, invoice.unlink):
            with self.subTest(action=action.__name__), self.assertRaises(UserError):
                action()
        with self.assertRaises(UserError):
            invoice.write({"partner_id": self.company.partner_id.id})
        invoice.write({"ref": "Customer PO 123"})
        self.assertEqual(invoice.state, "posted")

    def test_rejected_dte_releases_the_invoice(self):
        invoice, document = self._issue(610, scenario="rejected")
        self.assertEqual(document.fiscal_state, "rejected")
        invoice.button_draft()
        self.assertEqual(invoice.state, "draft")
