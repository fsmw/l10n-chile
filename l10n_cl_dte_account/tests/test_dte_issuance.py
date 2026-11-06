import base64
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_account.services.dte_issuance_service import (
    DteIssuanceService,
)
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase


@tagged("post_install", "-at_install")
class TestDteIssuance(DteInvoiceTestCase):
    def test_revised_signature_is_frozen_and_sent_without_another_folio(self):
        invoice = self._manager_invoice(self.make_invoice())
        self.validate_authorization(document_code="33", folio_start=540, folio_end=545)
        self.create_signing_profile()
        adapter = invoice._get_dte_issuance_adapter("33")
        document = DteIssuanceService().prepare(invoice, adapter, "33")
        original = document._get_signed_artifact()
        payload = base64.b64decode(original.content) + b"\n"
        current = original._create_signature_revision(original, payload, "tests/18.0")
        self.assertEqual(invoice.issue_dte(), document)
        request = document.artifact_ids.filtered(
            lambda item: item.role == "sii_request"
        )
        self.assertEqual(base64.b64decode(request.content), payload)
        self.assertEqual(document._get_signed_artifact(), current)
        self.assertEqual(len(self.env["l10n_cl.caf.folio"].search([])), 1)
        submission = self.env["l10n_cl.electronic.submission"].search(
            [("document_id", "=", document.id)]
        )
        submission._job_send_to_sii()
        self.assertEqual(submission.state, "submitted")
        self.assertTrue(original.action_verify_integrity())

    def _manager_invoice(self, invoice):
        return self.env["account.move"].with_user(self.dte_manager).browse(invoice.id)

    def test_issue_requires_posted_move(self):
        invoice = self.make_invoice(post=False)
        with self.assertRaisesRegex(UserError, "Only posted customer documents"):
            self._manager_invoice(invoice).issue_dte()

    def test_issue_validates_before_folio_reservation(self):
        invoice = self.make_invoice()
        invoice.company_id.partner_id.sudo().write({"vat": False})
        before = self.env["l10n_cl.caf.folio"].search_count([])
        with self.assertRaisesRegex(ValidationError, "valid Chilean RUT"):
            self._manager_invoice(invoice).issue_dte()
        self.assertEqual(self.env["l10n_cl.caf.folio"].search_count([]), before)

    def test_adapter_selected_by_document_type(self):
        invoice = self.make_invoice("33")
        adapter = self._manager_invoice(invoice)._get_dte_issuance_adapter("33")
        self.assertEqual(adapter.supported_document_codes, {"33", "34"})
        self.assertIsNotNone(
            self._manager_invoice(invoice)._get_dte_issuance_adapter("34")
        )

    def test_unsupported_type_has_actionable_error(self):
        invoice = self.make_invoice("33", post=False)
        invoice.l10n_latam_document_type_id = self.document_types["39"]
        with self.assertRaisesRegex(UserError, "not supported by this issuance flow"):
            self._manager_invoice(invoice)._get_dte_issuance_adapter("99")

    def test_issuance_rolls_back_on_stage_failure(self):
        invoice = self.make_invoice()
        self.validate_authorization(document_code="33", folio_start=600, folio_end=605)
        adapter = self._manager_invoice(invoice)._get_dte_issuance_adapter("33")
        before = self.env["l10n_cl.caf.folio"].search_count([])
        with patch.object(
            adapter,
            "compose_unsigned_document",
            side_effect=UserError("synthetic stage failure"),
        ):
            with self.assertRaisesRegex(UserError, "synthetic stage failure"):
                DteIssuanceService().issue(
                    self._manager_invoice(invoice), adapter, "33"
                )
        self.assertFalse(invoice.dte_document_ids)
        self.assertEqual(self.env["l10n_cl.caf.folio"].search_count([]), before)

    def test_repeated_issue_does_not_reserve_second_folio(self):
        invoice = self.make_invoice()
        self.validate_authorization(document_code="33", folio_start=610, folio_end=615)
        self.create_signing_profile()
        document = self._manager_invoice(invoice).issue_dte()
        first_folio = document.folio
        self._manager_invoice(invoice).issue_dte()
        self.assertEqual(document.folio, first_folio)
        self.assertEqual(len(invoice.dte_document_ids), 1)
        submission = self.env["l10n_cl.electronic.submission"].search(
            [("document_id", "=", document.id)], limit=1
        )
        jobs = (
            self.env["queue.job"]
            .sudo()
            .search([("identity_key", "=", f"dte-send-{submission.idempotency_key}")])
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(self.env["l10n_cl.caf.folio"].search_count([]), 1)

    def test_invoice_33_demo_flow_keeps_evidence(self):
        invoice = self.make_invoice()
        self.validate_authorization(document_code="33", folio_start=620, folio_end=625)
        self.create_signing_profile()
        document = self._manager_invoice(invoice).issue_dte()
        self.assertEqual(document.document_code, "33")
        self.assertEqual(document.preparation_state, "queued")
        self.assertGreater(document.folio, 0)
        self.assertTrue(document.payload_digest)
        self.assertEqual(
            set(document.artifact_ids.mapped("role")),
            {
                "caf_source",
                "source_snapshot",
                "unsigned_xml",
                "signed_xml",
                "sii_request",
            },
        )
        submission = self.env["l10n_cl.electronic.submission"].search(
            [("document_id", "=", document.id)]
        )
        self.assertEqual(len(submission), 1)
        submission._job_send_to_sii()
        submission.invalidate_recordset()
        self.assertTrue(submission.track_id)
        self.assertEqual(submission.state, "submitted")
        self.assertTrue(
            submission.document_id.artifact_ids.filtered(
                lambda a: a.role == "sii_response"
            )
        )

    def test_issue_requires_dte_group(self):
        invoice = self.make_invoice()
        with self.assertRaises(AccessError):
            self.env["account.move"].with_user(self.dte_user).browse(
                invoice.id
            ).issue_dte()
