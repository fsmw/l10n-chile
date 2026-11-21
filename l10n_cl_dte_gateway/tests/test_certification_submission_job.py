# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import json
from datetime import timedelta

import requests
from lxml import etree

from odoo import SUPERUSER_ID, fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.services.invoice_snapshot import InvoiceSnapshotService
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import DteXmlComposer
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase

from ..services.certification_document_transport import CertificationDocumentTransport
from ..services.production_gateway import ProductionSiiGateway
from .common_certification_request import (
    FakeSiiSession,
    configure_company_certification,
    create_sii_caf,
    dte_status_response,
    fake_sii,
)
from .common_production_request import ProductionRequestCase
from .test_certification_transport import upload_response

SII = "{http://www.sii.cl/SiiDte}"


class CertificationRequestCase(DteInvoiceTestCase):
    """Issue synthetic 33/34 DTEs in Certification with offline crypto."""

    def setUp(self):
        super().setUp()
        self.company = self.company.with_user(self.dte_manager)
        self.profile = configure_company_certification(self)

    def issue(self, code="33", folio=100):
        caf = create_sii_caf(self, "certification", code=code, folio_start=folio)
        document = self.create_document(document_code=code)
        caf.action_reserve_folio(document, f"certification-{code}-{folio}")
        invoice = self.make_invoice(code=code, post=False, taxable=code == "33")
        invoice.invoice_date = fields.Date.today()
        invoice.action_post()
        snapshot = InvoiceSnapshotService().capture_invoice(invoice, caf, folio, code)
        self._record(document, "source_snapshot", json.dumps(snapshot).encode())
        snapshot["ted_xml"] = DteXmlComposer().compose_ted(snapshot, caf)
        unsigned = DteXmlComposer().compose_unsigned_document(snapshot)
        self._record(document, "unsigned_xml", unsigned, "prepared")
        signed = SignatureService().sign_document(
            self.company, "certification", unsigned
        )
        self._record(document, "signed_xml", signed, "signed")
        self._record(document, "sii_request", signed, "queued")
        submission = (
            self.env["l10n_cl.electronic.submission"]
            .sudo()
            .create(
                {
                    "document_id": document.id,
                    "idempotency_key": f"certification-{code}-{folio}",
                }
            )
        )
        return document, submission, caf

    def _record(self, document, role, payload, state=None):
        evidence = self.create_artifact(document, role, payload)
        if state:
            document.action_record_transition(
                state, f"test_{state}", artifact_id=evidence.id
            )
        else:
            document.action_record_source_snapshot(evidence.id)

    @staticmethod
    def sends(submission):
        return submission.attempt_ids.filtered(lambda item: item.operation == "send")


@tagged("post_install", "-at_install")
class TestCertificationSubmissionJob(CertificationRequestCase):
    """Send 33/34 to maullin through the individual submission queue."""

    def _accepted_flow(self, code, folio):
        document, submission, _caf = self.issue(code, folio)
        session = FakeSiiSession(statuses=[dte_status_response(code)])
        with fake_sii(session):
            submission._job_send_to_sii()
            envelope = submission.transport_request_artifact_id
            # The envelope was bound before the upload that carried its bytes.
            self.assertEqual(session.uploads, [base64.b64decode(envelope.content)])
            self.assertEqual(self.sends(submission).request_digest, envelope.sha256)
            self.assertEqual(submission.state, "submitted", submission.sii_message)
            self.assertEqual(submission.track_id, "123")
            self.assertEqual(document.fiscal_state, "submitted")
            submission.with_context(
                queue_job__no_delay=True
            ).action_enqueue_status_query()
        root = etree.fromstring(session.uploads[0])
        self.assertEqual(len(root.findall(f"{SII}SetDTE/{SII}DTE")), 1)
        self.assertEqual(root.findtext(f"{SII}SetDTE/{SII}Caratula/{SII}NroResol"), "0")
        self.assertIn(f"<TpoDTE>{code}</TpoDTE>".encode(), session.uploads[0])
        self.assertTrue(
            all(url.startswith("https://maullin.sii.cl/") for url in session.urls)
        )
        self.assertEqual(submission.state, "accepted", submission.sii_message)
        self.assertEqual(document.fiscal_state, "accepted")
        roles = document.artifact_ids.mapped("role")
        self.assertIn("sii_response", roles)
        self.assertIn("status_response", roles)

    def test_invoice_33_is_sent_to_maullin_and_accepted_with_dte_evidence(self):
        self._accepted_flow("33", 100)

    def test_invoice_34_is_sent_to_maullin_and_accepted_with_dte_evidence(self):
        self._accepted_flow("34", 200)

    def test_track_id_without_dte_counters_is_not_acceptance(self):
        document, submission, _caf = self.issue()
        session = FakeSiiSession(
            statuses=[
                dte_status_response(state="SOK"),
                dte_status_response(accepted=2),
            ]
        )
        with fake_sii(session):
            submission._job_send_to_sii()
            submission._job_query_sii_status()
            self.assertEqual(submission.state, "processing")
            submission._job_query_sii_status()
        self.assertEqual(submission.state, "processing")
        self.assertNotEqual(document.fiscal_state, "accepted")

    def test_rejected_dte_result_is_recorded(self):
        document, submission, _caf = self.issue("34", 300)
        session = FakeSiiSession(
            statuses=[dte_status_response("34", accepted=0, rejected=1)]
        )
        with fake_sii(session):
            submission._job_send_to_sii()
            submission._job_query_sii_status()
        self.assertEqual(submission.state, "rejected")
        self.assertEqual(document.fiscal_state, "rejected")
        self.assertIn("EPR", submission.sii_message)

    def test_upload_rejection_is_final_and_explained(self):
        document, submission, _caf = self.issue()
        with fake_sii(FakeSiiSession(upload=upload_response(status="7"))):
            submission._job_send_to_sii()
        self.assertEqual(submission.state, "rejected")
        self.assertEqual(document.fiscal_state, "rejected")
        self.assertIn("Invalid schema", submission.sii_message)
        self.assertFalse(submission.track_id)

    def test_lost_upload_response_is_uncertain_and_blocks_resend(self):
        document, submission, _caf = self.issue()
        session = FakeSiiSession(upload=requests.ConnectionError("synthetic"))
        with fake_sii(session):
            submission._job_send_to_sii()
            self.assertEqual(submission.state, "outcome_unknown")
            self.assertEqual(document.fiscal_state, "outcome_unknown")
            self.assertFalse(submission._was_never_dispatched())
            self.assertTrue(
                submission.with_context(
                    queue_job__no_delay=True
                ).action_enqueue_submission()
            )
        self.assertEqual(session.upload_count(), 1)
        self.assertEqual(len(self.sends(submission)), 1)
        self.assertEqual(submission.state, "outcome_unknown")

    def test_failure_before_dispatch_retries_the_same_dte_folio_and_envelope(self):
        document, submission, caf = self.issue()
        signed = document._get_signed_artifact()
        with fake_sii(FakeSiiSession(seed_state="-07")) as session:
            submission._job_send_to_sii()
        self.assertEqual(submission.state, "error")
        self.assertEqual(session.upload_count(), 0)
        self.assertTrue(submission._was_never_dispatched())
        envelope = submission.transport_request_artifact_id
        self.assertTrue(envelope)
        with fake_sii(FakeSiiSession(upload=upload_response(status="5"))) as session:
            submission.with_context(
                queue_job__no_delay=True
            ).action_enqueue_submission()
        self.assertEqual(submission.state, "error", "status 5 was not processed")
        self.assertTrue(submission._was_never_dispatched())
        with fake_sii(FakeSiiSession()) as session:
            submission.with_context(
                queue_job__no_delay=True
            ).action_enqueue_submission()
            # Repeating the action after the receipt neither resends nor rebuilds.
            submission.with_context(
                queue_job__no_delay=True
            ).action_enqueue_submission()
        self.assertEqual(submission.state, "submitted", submission.sii_message)
        self.assertEqual(session.upload_count(), 1)
        self.assertEqual(session.uploads[0], base64.b64decode(envelope.content))
        self.assertEqual(submission.transport_request_artifact_id, envelope)
        self.assertEqual(document._get_signed_artifact(), signed)
        self.assertEqual(len(caf.folio_ids), 1)
        self.assertEqual(caf.folio_ids.document_id, document)
        self.assertEqual(len(self.sends(submission)), 3)

    def test_cron_queries_certification_submissions_on_maullin(self):
        document, submission, _caf = self.issue()
        session = FakeSiiSession(statuses=[dte_status_response()])
        with fake_sii(session):
            submission._job_send_to_sii()
            submission._write_internal(
                {"submitted_at": fields.Datetime.now() - timedelta(minutes=6)}
            )
            model = (
                self.env["l10n_cl.electronic.submission"]
                .with_user(SUPERUSER_ID)
                .with_context(queue_job__no_delay=True)
            )
            self.assertEqual(model._cron_poll_sii_status(), 1)
        self.assertEqual(submission.state, "accepted", submission.sii_message)
        self.assertEqual(document.fiscal_state, "accepted")
        self.assertTrue(session.urls[-1].endswith("/DTEWS/QueryEstUp.jws"))

    def test_dte_user_issuance_authenticates_through_the_workflow(self):
        _document, submission, _caf = self.issue()
        with fake_sii(FakeSiiSession()):
            submission.with_user(self.dte_user).sudo()._job_send_to_sii()
        self.assertEqual(submission.state, "submitted", submission.sii_message)

    def test_production_caf_blocks_certification_dispatch(self):
        _document, submission, caf = self.issue()
        self.env.cr.execute(
            "UPDATE l10n_cl_caf_authorization SET sii_environment = 'production' "
            "WHERE id = %s",
            [caf.id],
        )
        caf.invalidate_recordset()
        with fake_sii(FakeSiiSession()) as session:
            submission._job_send_to_sii()
        self.assertEqual(submission.state, "error")
        self.assertIn("Certification CAF", submission.sii_message)
        self.assertEqual(session.urls, [])

    def test_production_signing_profile_blocks_certification_dispatch(self):
        _document, submission, _caf = self.issue()
        self.profile.sudo().write({"active": False})
        self.create_signing_profile(company=self.company, environment="production")
        with fake_sii(FakeSiiSession()) as session:
            submission._job_send_to_sii()
        self.assertEqual(submission.state, "error")
        self.assertIn("Certification signing profile", submission.sii_message)
        self.assertEqual(session.urls, [])

    def test_factory_routes_certification_to_maullin_for_invoices_only(self):
        gateway = self.env["l10n_cl.sii.gateway.factory"].get_gateway("certification")
        self.assertIsInstance(gateway, ProductionSiiGateway)
        self.assertIsInstance(gateway._transport(), CertificationDocumentTransport)
        for code in ("33", "34"):
            self.assertTrue(gateway.check_document_code(code))
        with self.assertRaisesRegex(UserError, "33 and 34"):
            gateway.check_document_code("56")
        with self.assertRaisesRegex(UserError, "another environment"):
            gateway.validate_configuration(self.company, "production")


@tagged("post_install", "-at_install")
class TestProductionRejectsCertificationMaterial(ProductionRequestCase):
    """Production keeps refusing Certification CAFs and signing profiles."""

    def setUp(self):
        super().setUp()
        self.submission = self.submission.sudo()

    def test_certification_caf_blocks_production_dispatch(self):
        self.env.cr.execute(
            "UPDATE l10n_cl_caf_authorization SET sii_environment = 'certification' "
            "WHERE id = %s",
            [self.caf.id],
        )
        self.caf.invalidate_recordset()
        with fake_sii(FakeSiiSession()) as session:
            self.submission._job_send_to_sii()
        self.assertEqual(self.submission.state, "error")
        self.assertIn("Production CAF", self.submission.sii_message)
        self.assertEqual(session.urls, [])

    def test_certification_signing_profile_blocks_production_dispatch(self):
        profiles = (
            self.env["l10n_cl.dte.signing.profile"]
            .sudo()
            .search(
                [
                    ("company_id", "=", self.company.id),
                    ("sii_environment", "=", "production"),
                ]
            )
        )
        profiles.write({"active": False})
        self.create_signing_profile(company=self.company, environment="certification")
        with fake_sii(FakeSiiSession()) as session:
            self.submission._job_send_to_sii()
        self.assertEqual(self.submission.state, "error")
        self.assertIn("Production signing profile", self.submission.sii_message)
        self.assertEqual(session.urls, [])
