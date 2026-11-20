# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError

from ..services.caf_availability import available_folios
from .common import (
    BLOCKED_PAGE,
    REJECTION_PAGE,
    CafRequestTestCase,
    allocation_pages,
    login,
)

LOST = RuntimeError("synthetic lost response")


class TestCafRequest(CafRequestTestCase):
    def test_success_activates_validated_caf_with_evidence(self):
        request = self.new_request(quantity=10)
        responses = self.success(1, 10)
        with self.sii(*responses) as transport:
            request.action_execute()
        self.assertEqual(len(transport.assistant_calls), 5)
        self.assertEqual(request.state, "success")
        self.assertEqual((request.folio_start, request.folio_end), (1, 10))
        authorization = request.authorization_id
        self.assertEqual(authorization.state, "validated")
        self.assertEqual(authorization.sudo().sii_environment, "certification")
        self.assertEqual((authorization.folio_start, authorization.folio_end), (1, 10))
        attempt = request.attempt_ids
        self.assertEqual(attempt.number, 1)
        self.assertEqual(attempt.state, "success")
        self.assertTrue(attempt.dispatch_started)
        self.assertEqual(
            attempt.endpoint, "https://maullin.sii.cl/cvc_cgi/dte/of_solicita_folios"
        )
        evidence = request.evidence_ids
        response = base64.b64decode(evidence.response_file)
        self.assertEqual(response, responses[-1].body)
        self.assertEqual(evidence.response_digest, hashlib.sha256(response).hexdigest())
        self.assertEqual(authorization.caf_digest, evidence.caf_digest)
        self.assertEqual(
            available_folios(self.env, self.company, "33", "certification"), 10
        )

    def test_sii_rejection_fails_without_retry(self):
        request = self.new_request()
        pages = allocation_pages(1, 10)
        with self.sii(pages[0], pages[1], REJECTION_PAGE):
            request.action_execute()
        self.assertEqual(request.state, "failed")
        self.assertEqual(request.error_code, "SII_HARD_REJECTION")
        self.assertEqual(request.sii_error_code, "sii_rejected_request")
        self.assertEqual(request.failure_phase, "pre_dispatch")
        self.assertFalse(request.attempt_ids.dispatch_started)
        self.assertFalse(request.can_retry)
        self.assertFalse(request.can_reconcile)
        with self.assertRaises(UserError):
            request.action_retry()

    def test_unused_folio_block_page(self):
        request = self.new_request()
        with self.sii(login(), BLOCKED_PAGE):
            request.action_execute()
        self.assertEqual(request.state, "failed")
        self.assertEqual(request.error_code, "SII_FOLIO_BLOCKED")
        self.assertIn("unused", request.error_message)
        self.assertFalse(request.can_retry)

    def test_quantity_limit_records_sii_maximum(self):
        request = self.new_request()
        pages = allocation_pages(1, 10, confirmation={"MAX_AUTOR": "4"})
        with self.sii(*pages[:3]):
            request.action_execute()
        self.assertEqual(request.error_code, "SII_QUANTITY_LIMIT")
        self.assertEqual(request.sii_maximum_quantity, 4)

    def test_malformed_caf_download_is_uncertain(self):
        request = self.new_request()
        broken = self.caf_response(1, 10)
        broken = broken.__class__(200, {}, b"<AUTORIZACION><CAF/></AUTORIZACION>")
        with self.sii(*allocation_pages(1, 10), broken):
            request.action_execute()
        self.assertEqual(request.state, "uncertain")
        self.assertEqual(request.sii_error_code, "caf_download_or_validation_uncertain")
        self.assertFalse(request.authorization_id)

    def test_caf_failing_local_validation_is_not_activated(self):
        request = self.new_request()
        with self.sii(*self.future_caf(1, 10)):
            request.action_execute()
        self.assertEqual(request.state, "failed")
        self.assertEqual(request.error_code, "CAF_VALIDATION_FAILED")
        self.assertEqual(request.failure_phase, "validation")
        self.assertFalse(request.authorization_id)
        self.assertFalse(
            self.env["l10n_cl.caf.authorization"].search(
                [("company_id", "=", self.company.id), ("state", "=", "validated")]
            )
        )
        evidence = request.evidence_ids
        self.assertEqual(evidence.validation_error_code, "CAF_VALIDATION_FAILED")
        self.assertTrue(evidence.caf_file)
        self.assertFalse(request.can_retry)
        self.assertTrue(request.can_reconcile)

    def test_post_dispatch_failure_is_uncertain_and_never_retried(self):
        request = self.new_request()
        pages = allocation_pages(1, 10)
        with self.sii(*pages[:3], LOST, *self.success()) as transport:
            request.action_execute()
            self.assertEqual(request.state, "uncertain")
            self.assertEqual(request.error_code, "SII_DISPATCH_UNCERTAIN")
            self.assertEqual((request.folio_start, request.folio_end), (1, 10))
            self.assertTrue(request.attempt_ids.dispatch_started)
            self.assertFalse(request.can_retry)
            with self.assertRaises(UserError):
                request.action_retry()
            with self.assertRaises(UserError):
                request.action_execute()
            self.Request._cron_process_requests()
        self.assertEqual(len(transport.assistant_calls), 4)
        self.assertEqual(request.attempt_count, 1)

    def test_pre_dispatch_failure_is_retried_within_bound(self):
        request = self.new_request()
        with self.sii(LOST, LOST, LOST) as transport:
            request.action_execute()
            self.assertEqual(request.state, "failed")
            self.assertEqual(request.error_code, "SII_TRANSPORT_ERROR")
            self.assertFalse(request.attempt_ids.dispatch_started)
            self.assertTrue(request.can_retry)
            request.action_retry()
            request.action_retry()
            self.assertEqual(request.attempt_count, 3)
            self.assertFalse(request.can_retry)
            with self.assertRaises(UserError):
                request.action_retry()
        self.assertEqual(len(transport.assistant_calls), 3)

    def test_retry_after_pre_dispatch_failure_succeeds(self):
        request = self.new_request()
        with self.sii(LOST, *self.success()):
            request.action_execute()
            request.action_retry()
        self.assertEqual(request.state, "success")
        self.assertEqual(request.attempt_ids.mapped("number"), [2, 1])

    def test_recover_uncertain_request_from_sii(self):
        request = self.new_request()
        pages = allocation_pages(1, 10)
        with self.sii(*pages[:3], LOST, *self.recovery_pages(1, 10)) as transport:
            request.action_execute()
            self.assertTrue(request.can_recover)
            request.action_recover_from_sii()
        self.assertEqual(request.state, "reconciled")
        self.assertEqual(request.authorization_id.state, "validated")
        self.assertEqual(
            (request.authorization_id.folio_start, request.authorization_id.folio_end),
            (1, 10),
        )
        recovery_urls = [call["url"] for call in transport.assistant_calls[5:]]
        self.assertTrue(all("/rf_" in url for url in recovery_urls))

    def test_recovery_not_found_keeps_request_uncertain(self):
        request = self.new_request()
        pages = allocation_pages(1, 10)
        recovery = self.recovery_pages(50, 59)[:3]
        with self.sii(*pages[:3], LOST, *recovery):
            request.action_execute()
            request.action_recover_from_sii()
        self.assertEqual(request.state, "uncertain")
        self.assertEqual(request.recovery_error_code, "caf_recovery_not_found")

    def test_recovery_reuses_an_already_imported_caf(self):
        request = self.new_request()
        pages = allocation_pages(1, 10)
        with self.sii(*pages[:3], LOST) as transport:
            request.action_execute()
            imported = self.import_caf(1, 10)
            request.action_recover_from_sii()
        self.assertEqual(request.state, "reconciled")
        self.assertEqual(request.authorization_id, imported)
        self.assertEqual(len(transport.assistant_calls), 4)

    def test_manual_reconcile_of_uncertain_request(self):
        request = self.new_request()
        with self.sii(*allocation_pages(1, 10)[:3], LOST):
            request.action_execute()
        other_type = self.import_caf(1, 10, document_code="61")
        request.reconciliation_authorization_id = other_type
        with self.assertRaises(ValidationError):
            request.action_reconcile()
        recovered = self.import_caf(1, 10)
        request.reconciliation_authorization_id = recovered
        request.action_reconcile()
        self.assertEqual(request.state, "reconciled")
        self.assertEqual(request.authorization_id, recovered)
        self.assertEqual(request.reconciled_by, self.dte_manager)

    def test_only_approved_requests_run_and_states_are_protected(self):
        request = self.new_request(approve=False)
        with self.sii() as transport:
            with self.assertRaises(UserError):
                request.action_execute()
        self.assertFalse(transport.calls)
        with self.assertRaises(UserError):
            request.write({"state": "approved"})
        request.write({"requested_quantity": 20})
        request.action_approve()
        with self.assertRaises(UserError):
            request.write({"requested_quantity": 30})
        request.action_cancel()
        self.assertEqual(request.state, "cancelled")

    def test_create_is_idempotent_by_key(self):
        first = self.new_request(approve=False, idempotency_key="manual-1")
        second = self.new_request(approve=False, idempotency_key="manual-1")
        self.assertEqual(first, second)
        third = self.new_request(approve=False)
        self.assertNotEqual(first, third)

    def test_interrupted_running_requests_are_closed_safely(self):
        dispatched = self.new_request()
        quiet = self.new_request()
        for request, sent in ((dispatched, True), (quiet, False)):
            self.env["l10n_cl.caf.request.attempt"].create(
                {
                    "request_id": request.id,
                    "number": 1,
                    "started_at": fields.Datetime.now(),
                    "dispatch_started": sent,
                }
            )
            request._write_internal(
                {
                    "state": "running",
                    "attempt_count": 1,
                    "requested_at": fields.Datetime.now() - timedelta(hours=2),
                }
            )
        self.Request._recover_stale_running()
        self.assertEqual(dispatched.state, "uncertain")
        self.assertEqual(quiet.state, "failed")
        self.assertTrue(quiet.can_retry)
