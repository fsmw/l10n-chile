# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import timedelta
from unittest.mock import Mock, patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from ..services.certification_auth import SiiTransportError
from ..services.production_gateway import ProductionSiiGateway
from ..services.production_response_parser import parse_status, parse_upload
from ..services.production_transport import ProductionSiiTransport
from .common_production_request import ProductionRequestCase
from .test_certification_transport import status_response, upload_response


@tagged("post_install", "-at_install")
class TestProductionSubmissionJob(ProductionRequestCase):
    """Run the production send and poll jobs against recorded SII replies."""

    def setUp(self):
        super().setUp()
        self.submission = self.submission.sudo()

    def _upload(self, status="0"):
        raw = upload_response(status=status).replace(
            b"<RUTCOMPANY>12345678-5", f"<RUTCOMPANY>{self.company.vat}".encode()
        )
        return parse_upload(raw, self.company.vat, self.company.dte_sii_sender_vat)

    def _send(self, **mock):
        with patch.object(ProductionSiiTransport, "send_envelope", **mock) as send:
            self.submission._job_send_to_sii()
        return send

    def _send_attempts(self):
        return self.submission.attempt_ids.filtered(
            lambda attempt: attempt.operation == "send"
        )

    def test_upload_binds_the_envelope_and_records_the_track_id(self):
        send = self._send(return_value=self._upload())
        envelope = self.submission.transport_request_artifact_id
        self.assertEqual(self.submission.state, "submitted")
        self.assertEqual(self.submission.track_id, "123")
        self.assertEqual(self.document.fiscal_state, "submitted")
        self.assertTrue(envelope)
        self.assertEqual(self._send_attempts().request_digest, envelope.sha256)
        self.assertIn(b"<EnvioDTE", send.call_args.args[2])

    def test_upload_rejection_is_final_and_explained(self):
        self._send(return_value=self._upload(status="7"))
        self.assertEqual(self.submission.state, "rejected")
        self.assertEqual(self.document.fiscal_state, "rejected")
        self.assertIn("Invalid schema", self.submission.sii_message)
        self.assertFalse(self.submission.track_id)

    def test_failures_before_dispatch_can_be_queued_again(self):
        with patch.object(
            ProductionSiiGateway,
            "validate_configuration",
            side_effect=UserError("Missing resolution"),
        ):
            self.submission._job_send_to_sii()
        self.assertEqual(self.submission.state, "error")
        self.assertEqual(self.submission.sii_message, "Missing resolution")
        self.assertFalse(self._send_attempts())
        self.assertEqual(self.document.fiscal_state, "unknown")

        with patch.object(
            ProductionSiiTransport, "send_envelope", return_value=self._upload()
        ):
            self.submission.with_context(
                queue_job__no_delay=True
            ).action_enqueue_submission()
        self.assertEqual(self.submission.state, "submitted")

    def test_authentication_failure_is_not_an_unknown_outcome(self):
        offline = SiiTransportError("SII did not authorize a seed", dispatched=False)
        self._send(side_effect=offline)
        self.assertEqual(self.submission.state, "error")
        self.assertEqual(self._send_attempts().state, "failed")
        self.assertEqual(self.document.fiscal_state, "unknown")

        self.submission._write_internal({"state": "pending"})
        self._send(return_value=self._upload())
        self.assertEqual(self.submission.state, "submitted")
        self.assertEqual(len(self._send_attempts()), 2)

    def test_locked_sii_system_is_a_retryable_error(self):
        locked = SiiTransportError("System locked", dispatched=False, code="9")
        self._send(side_effect=locked)
        self.assertEqual(self.submission.state, "error")
        self.assertEqual(self.document.fiscal_state, "unknown")
        self.assertTrue(self.submission._was_never_dispatched())

    def test_unapplicable_receipt_is_kept_as_unknown(self):
        with patch.object(
            type(self.submission),
            "_apply_production_outcome",
            side_effect=UserError("Receipt could not be verified"),
        ):
            self._send(return_value=self._upload())
        self.assertEqual(self.submission.state, "outcome_unknown")
        self.assertEqual(self.document.fiscal_state, "outcome_unknown")
        self.assertEqual(self._send_attempts().state, "unknown")
        self.assertFalse(self.submission._was_never_dispatched())

    def test_lost_upload_receipt_stays_unknown_and_blocks_resend(self):
        lost = SiiTransportError("Timeout", dispatched=True, code="timeout")
        self._send(side_effect=lost)
        self.assertEqual(self.submission.state, "outcome_unknown")
        self.assertIn("Timeout", self.submission.sii_message)
        self.assertFalse(self.submission._was_never_dispatched())

    def _poll(self, *responses):
        self._send(return_value=self._upload())
        results = [
            {**parse_status(raw, "123"), "is_simulated": False} for raw in responses
        ]
        with patch.object(
            ProductionSiiTransport, "query_envelope", side_effect=results
        ):
            for _response in responses:
                self.submission._job_query_sii_status()

    def test_observations_are_kept_on_acceptance(self):
        self._poll(status_response(accepted=0, observed=1))
        self.assertEqual(self.submission.state, "accepted")
        self.assertEqual(self.document.fiscal_state, "accepted")
        self.assertTrue(self.submission.sii_has_observations)
        self.assertIn("observations", self.submission.sii_message)

    def test_failed_query_keeps_the_confirmed_state(self):
        self._poll(status_response(code="001"))
        self.assertEqual(self.submission.state, "submitted")
        self.assertEqual(self.document.fiscal_state, "submitted")
        self.assertIn("001", self.submission.sii_message)
        self.assertEqual(self.submission._query_attempts().state, "failed")

    def test_poller_waits_for_the_backoff_before_asking_again(self):
        self._send(return_value=self._upload())
        self.submission._write_internal(
            {"submitted_at": fields.Datetime.now() - timedelta(minutes=6)}
        )
        model = self.env["l10n_cl.electronic.submission"]
        with patch.object(type(model), "with_delay") as delay:
            self.assertEqual(model._cron_poll_sii_status(), 1)
            delay.return_value._job_query_sii_status.assert_called_once()
            self.submission._write_internal({"last_checked_at": fields.Datetime.now()})
            self.assertEqual(model._cron_poll_sii_status(), 0)


@tagged("post_install", "-at_install")
class TestProductionTokenRefresh(ProductionRequestCase):
    """SII token rejections refresh the token once and repeat the request."""

    def _transport(self, *replies, operation="post"):
        auth = Mock()
        auth.token.return_value = "token"
        getattr(auth, operation).side_effect = replies
        return ProductionSiiTransport(authentication_factory=lambda: auth), auth

    def _receipt(self, status):
        return upload_response(status=status).replace(
            b"<RUTCOMPANY>12345678-5", f"<RUTCOMPANY>{self.company.vat}".encode()
        )

    def test_upload_retries_once_after_not_authenticated(self):
        transport, auth = self._transport(self._receipt("5"), self._receipt("0"))
        sender = self.company.dte_sii_sender_vat
        with patch.object(
            transport, "validate_request", return_value=(self.company.vat, sender)
        ):
            result = transport.send_envelope(self.company, "production", b"<x/>", "k")
        self.assertEqual(result["state"], "submitted")
        auth.invalidate_token.assert_called_once_with(self.company)
        self.assertEqual(auth.post.call_count, 2)

    def test_repeated_not_authenticated_upload_is_not_a_rejection(self):
        transport, auth = self._transport(self._receipt("5"), self._receipt("5"))
        sender = self.company.dte_sii_sender_vat
        with patch.object(
            transport, "validate_request", return_value=(self.company.vat, sender)
        ):
            with self.assertRaises(SiiTransportError) as caught:
                transport.send_envelope(self.company, "production", b"<x/>", "k")
        self.assertFalse(caught.exception.dispatched)
        self.assertEqual(caught.exception.code, "upload_status_5")

    def test_query_retries_once_after_token_rejection(self):
        transport, auth = self._transport(
            status_response(code="002"),
            status_response(accepted=1),
            operation="soap",
        )
        result = transport.query_envelope(self.company, "production", "123")
        self.assertEqual(result["state"], "accepted")
        auth.invalidate_token.assert_called_once_with(self.company)
