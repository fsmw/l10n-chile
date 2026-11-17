# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
from unittest import TestCase
from unittest.mock import patch

import psycopg2

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from ..services.certification_auth import SiiTransportError
from ..services.production_document_outcome import ProductionDocumentOutcome
from ..services.production_response_parser import parse_upload
from .common_production_request import ProductionRequestCase
from .test_certification_transport import upload_response


@tagged("post_install", "-at_install")
class TestProductionOutcomeApplication(ProductionRequestCase):
    def setUp(self):
        super().setUp()
        self.envelope = self.submission._prepare_production_request()
        self.submission._write_internal({"state": "sending"})
        self.attempt = self._attempt(self.submission)
        self.events = self.document.event_ids
        self.artifacts = self.document.artifact_ids

    def _attempt(self, submission, operation="send", digest=None):
        attempts = self.env["l10n_cl.submission.attempt"].sudo()
        return attempts.create(
            {
                "submission_id": submission.id,
                "operation": operation,
                "request_digest": digest or self.envelope.sha256,
            }
        )

    def _result(self, status="0"):
        raw = upload_response(status=status).replace(
            b"<RUTCOMPANY>12345678-5", f"<RUTCOMPANY>{self.company.vat}".encode()
        )
        result = parse_upload(raw, self.company.vat, self.company.dte_sii_sender_vat)
        return {**result, "is_simulated": False}

    def _assert_untouched(self):
        self.assertEqual(self.submission.state, "sending")
        self.assertEqual(self.document.fiscal_state, "unknown")
        self.assertEqual(self.attempt.state, "started")
        self.assertFalse(self.attempt.completed_at or self.attempt.response_digest)
        self.assertEqual(self.document.artifact_ids, self.artifacts)
        self.assertEqual(self.document.event_ids, self.events)

    def _assert_outcome(self, state, raw=None):
        self.assertEqual(self.submission.state, state)
        self.assertEqual(self.document.fiscal_state, state)
        expected = "unknown" if state == "outcome_unknown" else "completed"
        self.assertEqual(self.attempt.state, expected)
        self.assertTrue(self.attempt.completed_at)
        events = self.document.event_ids - self.events
        response = self.document.artifact_ids - self.artifacts
        self.assertEqual(len(events), 1)
        self.assertEqual(events.to_state, state)
        self.assertEqual(events.artifact_id, response)
        self.assertEqual(len(response), int(raw is not None))
        self.assertEqual(self.attempt.response_digest, response.sha256)
        if raw is not None:
            self.assertEqual(response.role, "sii_response")
            self.assertEqual(base64.b64decode(response.content), raw)
            self.assertEqual(response.sha256, hashlib.sha256(raw).hexdigest())
        if state != "submitted":
            self.assertFalse(self.submission.track_id or self.submission.submitted_at)

    def test_upload_retains_evidence_before_normalization(self):
        result = self._result()
        native = ProductionDocumentOutcome().normalize_upload

        def normalize(value, *, document_code):
            response = self.document.artifact_ids - self.artifacts
            self.assertEqual(len(response), 1)
            self.assertEqual(base64.b64decode(response.content), result["raw_response"])
            response.action_verify_integrity()
            return native(value, document_code=document_code)

        with patch.object(
            ProductionDocumentOutcome, "normalize_upload", side_effect=normalize
        ) as policy:
            self.submission._apply_production_outcome(self.attempt, result, "send")
            policy.assert_called_once()
        self._assert_outcome("submitted", result["raw_response"])
        self.assertEqual(self.submission.track_id, "123")
        self.assertTrue(self.submission.submitted_at)

    def test_rejection_has_no_fabricated_submitted_event(self):
        result = self._result("7")
        self.submission._apply_production_outcome(self.attempt, result, "send")
        self._assert_outcome("rejected", result["raw_response"])
        self.assertEqual((self.document.event_ids - self.events).from_state, "unknown")

    def test_invalid_results_retain_raw_and_remain_unknown(self):
        valid = self._result()
        foreign = valid["raw_response"].replace(
            self.company.vat.encode(), b"76864280-K"
        )
        for changes in (
            {"is_simulated": True},
            {"track_id": "999"},
            {"transport_code": "8"},
            {"state": "rejected", "transport_code": "7", "track_id": False},
            {"raw_response": b"<invalid"},
            {"raw_response": foreign},
            {"raw_response": None},
        ):
            with self.subTest(changes=changes), self.cr.savepoint() as savepoint:
                result = {**valid, **changes}
                self.submission._apply_production_outcome(self.attempt, result, "send")
                self._assert_outcome("outcome_unknown", result["raw_response"])
                self.assertTrue(self.attempt.error_summary)
                savepoint.rollback()

    def test_database_and_unexpected_lifecycle_errors_roll_back_helper(self):
        for model, method, error in (
            (self.document, "action_record_transition", psycopg2.OperationalError),
            (self.document, "action_record_transition", RuntimeError),
            (self.submission, "_write_internal", RuntimeError),
        ):
            with patch.object(type(model), method, side_effect=error):
                # Native unittest avoids Odoo's assertRaises savepoint masking leaks.
                with TestCase.assertRaises(self, error):
                    self.submission._apply_production_outcome(
                        self.attempt, self._result(), "send"
                    )
                self._assert_untouched()

    def test_invalid_attempts_and_operations_fail_closed(self):
        with TestCase.assertRaises(self, UserError):
            self.submission._apply_production_outcome(self.attempt, None, "send")
        with self.cr.savepoint() as savepoint:
            # Corrupted retained identity must fail even with one started send.
            self.attempt.flush_recordset()
            self.cr.execute(
                "UPDATE l10n_cl_submission_attempt "
                "SET request_digest = %s WHERE id = %s",
                ["0" * 64, self.attempt.id],
            )
            self.attempt.invalidate_recordset()
            with TestCase.assertRaises(self, UserError):
                self.submission._apply_production_outcome(
                    self.attempt, self._result(), "send"
                )
            self._assert_untouched()
            savepoint.rollback()
        document_id = self.create_document().id
        foreign = self.submission.create(
            {"document_id": document_id, "idempotency_key": "foreign"}
        )
        finished = self._attempt(self.submission)
        finished._complete("completed")
        for attempt, operation in (
            (self._attempt(foreign), "send"),
            (self._attempt(self.submission, "query"), "send"),
            (finished, "send"),
            (self.attempt, "query"),
            (self.attempt, "reconcile"),
        ):
            with self.subTest(attempt=attempt.id, operation=operation):
                before = attempt.state
                with TestCase.assertRaises(self, UserError):
                    self.submission._apply_production_outcome(
                        attempt, self._result(), operation
                    )
                self.assertEqual(attempt.state, before)
                self._assert_untouched()

    def test_original_user_without_source_access_cannot_apply_outcome(self):
        submission = self.submission.with_user(self.dte_manager)
        with TestCase.assertRaises(self, AccessError):
            submission._apply_production_outcome(self.attempt, self._result(), "send")
        self._assert_untouched()

    def test_transport_errors_never_promote_receipts(self):
        for dispatched, raw, code in (
            (True, self._result()["raw_response"], "receipt_unknown"),
            (False, None, "offline"),
        ):
            with self.subTest(dispatched=dispatched), self.cr.savepoint() as savepoint:
                error = SiiTransportError(
                    "Transport failed", dispatched=dispatched, response=raw, code=code
                )
                self.submission._apply_production_outcome(self.attempt, error, "send")
                self._assert_outcome("outcome_unknown", raw)
                self.assertEqual(self.attempt.error_code, code)
                self.assertIn("Transport failed", self.attempt.error_summary)
                self.assertIn(f"dispatched={dispatched}", self.attempt.error_summary)
                self.assertEqual(len(self.submission.attempt_ids), 1)
                savepoint.rollback()
