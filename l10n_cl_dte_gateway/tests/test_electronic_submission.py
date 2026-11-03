from unittest.mock import patch

from .common import GatewayTestCase


class TestElectronicSubmission(GatewayTestCase):
    def test_enqueue_persists_before_job(self):
        _document, submission = self.create_submission()

        submission.action_enqueue_submission()

        job = self.env["queue.job"].search(
            [("identity_key", "=", f"dte-send-{submission.idempotency_key}")]
        )
        self.assertEqual(len(job), 1)
        self.assertEqual(submission.state, "pending")

    def test_enqueue_idempotent_on_retry(self):
        _document, submission = self.create_submission()

        submission.action_enqueue_submission()
        submission.action_enqueue_submission()

        jobs = self.env["queue.job"].search(
            [("identity_key", "=", f"dte-send-{submission.idempotency_key}")]
        )
        self.assertEqual(len(jobs), 1)

    def test_retry_preserves_previous_evidence(self):
        document, submission = self.create_submission()
        self.send_without_external_xml_validation(submission)
        response = document.artifact_ids.filtered(
            lambda item: item.role == "sii_response"
        )

        submission.action_enqueue_submission()
        self.assertTrue(submission.action_enqueue_submission())

        self.assertEqual(submission.state, "submitted")
        self.assertEqual(len(response), 1)
        self.assertTrue(response.action_verify_integrity())
        self.assertEqual(len(submission.attempt_ids), 1)

    def test_unknown_result_reconciled_once(self):
        document, submission = self.create_submission(
            scenario="unknown_then_reconciled"
        )
        self.send_without_external_xml_validation(submission)
        prior_response = document.artifact_ids.filtered(
            lambda item: item.role == "sii_response"
        )
        self.assertEqual(submission.state, "outcome_unknown")
        submission._job_reconcile_sii_submission()
        submission._job_reconcile_sii_submission()

        self.assertEqual(submission.state, "submitted")
        self.assertTrue(submission.track_id.startswith("DEMO-"))
        self.assertEqual(len(submission.attempt_ids), 2)
        self.assertEqual(
            len(
                document.artifact_ids.filtered(lambda item: item.role == "sii_response")
            ),
            2,
        )
        self.assertTrue(prior_response.action_verify_integrity())

    def test_enqueue_failure_is_atomic(self):
        _document, submission = self.create_submission()

        with patch.object(type(submission), "with_delay", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                submission.action_enqueue_submission()

        self.assertEqual(submission.state, "pending")
        self.assertFalse(submission.attempt_ids)
