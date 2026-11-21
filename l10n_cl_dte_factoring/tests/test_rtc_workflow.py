# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hashlib

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_gateway.services.sii_authentication import (
    CertificationAuthentication,
    ProductionSiiAuthentication,
)

from ..services.rtc_transport import RtcTransport
from .common import (
    FactoringTestCase,
    assignment_registry,
    upload_receipt,
    upload_status,
)


@tagged("post_install", "-at_install")
class TestRtcWorkflow(FactoringTestCase):
    def send(self, assignment):
        assignment.with_user(self.dte_manager)._job_send_aec()

    def test_transport_is_bound_to_real_sii_hosts(self):
        certification = RtcTransport("certification")
        self.assertIs(certification.authentication_factory, CertificationAuthentication)
        self.assertEqual(CertificationAuthentication.base_url, "https://maullin.sii.cl")
        production = RtcTransport("production")
        self.assertIs(production.authentication_factory, ProductionSiiAuthentication)
        with self.assertRaises(ValidationError):
            RtcTransport("demo")

    def test_company_mode_change_blocks_the_upload(self):
        assignment = self.signed_assignment()
        self.company.sudo().write({"dte_sii_environment": "certification"})
        with self.rtc() as auth:
            self.send(assignment)
        self.assertEqual(auth.session.calls, [])
        self.assertEqual(assignment.state, "error")

    def test_upload_success_keeps_receipt_and_track_id(self):
        assignment = self.signed_assignment()
        aec = self.aec_bytes(assignment)
        receipt = upload_receipt()
        with self.rtc(receipt) as auth:
            self.send(assignment)

        url, request = auth.session.calls[0]
        self.assertEqual(url, "https://palena.sii.cl/cgi_rtc/RTC/RTCAnotEnvio.cgi")
        self.assertEqual(request["files"]["archivo"][1], aec)
        self.assertEqual(request["data"]["rutCompany"], "12345678")
        self.assertEqual(request["data"]["dvCompany"], "5")
        self.assertEqual(request["headers"]["Cookie"], "TOKEN=TOKEN1")
        self.assertEqual(request["timeout"], (10, 60))
        self.assertFalse(request["allow_redirects"])
        self.assertEqual(assignment.state, "submitted")
        self.assertEqual(assignment.track_id, "38936")
        attempt = assignment.attempt_ids
        self.assertEqual(attempt.state, "completed")
        self.assertEqual(attempt.request_digest, hashlib.sha256(aec).hexdigest())
        response = assignment.evidence_ids.filtered(
            lambda item: item.role == "upload_response"
        )
        self.assertEqual(response.sha256, hashlib.sha256(receipt).hexdigest())
        self.assertEqual(attempt.response_digest, response.sha256)
        self.assertEqual(auth.closed, 1)

    def test_rejected_upload_is_final(self):
        assignment = self.signed_assignment()
        with self.rtc(upload_receipt(status="1", track_id="")):
            self.send(assignment)
        self.assertEqual(assignment.state, "rejected")
        self.assertEqual(assignment.sii_status, "1")
        self.assertFalse(assignment.track_id)

    def test_expired_token_is_refreshed_once(self):
        assignment = self.signed_assignment()
        with self.rtc(
            upload_receipt(status="5", track_id=""), upload_receipt()
        ) as auth:
            self.send(assignment)
        self.assertEqual(auth.invalidated, 1)
        self.assertEqual(auth.session.calls[1][1]["headers"]["Cookie"], "TOKEN=TOKEN2")
        self.assertEqual(assignment.state, "submitted")

    def test_internal_error_receipt_can_be_sent_again(self):
        assignment = self.signed_assignment()
        with self.rtc(upload_receipt(status="9", track_id="")):
            self.send(assignment)
        self.assertEqual(assignment.state, "error")
        self.assertEqual(assignment.attempt_ids.state, "failed")
        assignment.action_send()
        self.assertEqual(assignment.state, "signed")

    def test_lost_answer_is_uncertain_and_never_resent(self):
        assignment = self.signed_assignment()
        with self.rtc(self.connection_lost()) as auth:
            self.send(assignment)
        self.assertEqual(assignment.state, "outcome_unknown")
        self.assertEqual(assignment.attempt_ids.state, "unknown")
        self.assertEqual(len(auth.session.calls), 1)

        with self.rtc() as auth:
            self.send(assignment)
            with self.assertRaises(Exception):  # noqa: B017 - any refusal is fine
                assignment.action_send()
        self.assertEqual(auth.session.calls, [])
        self.assertEqual(len(assignment.attempt_ids), 1)
        self.assertFalse(
            self.env["queue.job"]
            .sudo()
            .search([("identity_key", "=", f"aec-send-{assignment.idempotency_key}")])
        )

    def test_malformed_receipt_after_upload_is_uncertain(self):
        assignment = self.signed_assignment()
        with self.rtc(b"<html>gateway timeout</html>"):
            self.send(assignment)
        self.assertEqual(assignment.state, "outcome_unknown")
        self.assertIn("upload_response", assignment.evidence_ids.mapped("role"))

    def test_send_is_queued_once(self):
        assignment = self.signed_assignment()
        assignment.action_send()
        assignment.action_send()
        jobs = (
            self.env["queue.job"]
            .sudo()
            .search([("identity_key", "=", f"aec-send-{assignment.idempotency_key}")])
        )
        self.assertEqual(len(jobs), 1)

    def test_status_query_marks_the_invoice_assigned(self):
        assignment = self.signed_assignment()
        with self.rtc(
            upload_receipt(), upload_status("SOK"), upload_status("EOK")
        ) as auth:
            self.send(assignment)
            assignment.action_query_status()
            self.assertEqual(assignment.state, "processing")
            assignment.action_query_status()
        url, request = auth.session.calls[1]
        self.assertEqual(url, "https://palena.sii.cl/DTEWS/services/wsRPETCConsulta")
        self.assertIn(b"<TrackId", request["data"])
        self.assertEqual(assignment.state, "accepted")
        self.assertEqual(assignment.sii_status, "EOK")
        self.invoice.invalidate_recordset()
        self.assertEqual(self.invoice.dte_assignment_state, "assigned")
        self.assertEqual(self.invoice.dte_assignee_id, self.factor)
        self.assertEqual(self.invoice.dte_assignment_track_id, "38936")
        roles = assignment.evidence_ids.mapped("role")
        self.assertEqual(roles.count("status_response"), 2)

    def test_status_query_reports_rejection(self):
        assignment = self.signed_assignment()
        with self.rtc(upload_receipt(), upload_status("RCS")):
            self.send(assignment)
            assignment.action_query_status()
        self.assertEqual(assignment.state, "rejected")
        self.invoice.invalidate_recordset()
        self.assertEqual(self.invoice.dte_assignment_state, "none")

    def test_failed_status_query_keeps_state(self):
        assignment = self.signed_assignment()
        with self.rtc(upload_receipt(), self.connection_lost()):
            self.send(assignment)
            assignment.action_query_status()
        self.assertEqual(assignment.state, "submitted")
        query = assignment.attempt_ids.filtered(lambda item: item.operation == "query")
        self.assertEqual(query.state, "failed")

    def test_registry_reconciles_an_unknown_upload(self):
        assignment = self.signed_assignment()
        with self.rtc(self.connection_lost(), assignment_registry("2", "No cedido")):
            self.send(assignment)
            assignment.action_reconcile()
        self.assertEqual(assignment.state, "outcome_unknown")
        with self.rtc(assignment_registry()) as auth:
            assignment.action_reconcile()
        self.assertIn(b"<FolioDoc", auth.session.calls[0][1]["data"])
        self.assertEqual(assignment.state, "accepted")
        self.assertEqual(
            assignment.attempt_ids.filtered(
                lambda item: item.operation == "reconcile"
            ).mapped("state"),
            ["completed", "completed"],
        )
