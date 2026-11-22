# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from contextlib import contextmanager
from unittest.mock import patch

import psycopg2

from odoo import modules
from odoo.tests import tagged

from ..services.certification_auth import SiiTransportError
from ..services.certification_document_transport import CertificationDocumentTransport
from ..services.production_transport import ProductionSiiTransport
from .common_production_request import ProductionRequestCase
from .test_certification_submission_job import CertificationRequestCase


class WorkerLost(BaseException):
    """The worker died after the upload: no handler runs, its transaction is lost."""


class DurableSendClaimMixin:
    """SII may have received an envelope whose local transaction was lost."""

    transport = None
    # Odoo collects only methods defined on the concrete class otherwise.
    allow_inherited_tests_method = True

    def _upload(self, side_effect):
        return patch.object(self.transport, "send_envelope", side_effect=side_effect)

    def _sends(self):
        return self.submission.attempt_ids.filtered(
            lambda attempt: attempt.operation == "send"
        )

    def _lose_transaction_after_upload(self):
        uploads = []

        def upload(*args, **kwargs):
            uploads.append(args[2])
            raise WorkerLost

        with self._upload(upload):
            self._expect_loss(WorkerLost)
        self.submission.invalidate_recordset()
        return uploads

    def _expect_loss(self, error_class):
        # Odoo's assertRaises rolls back a savepoint, which would also undo the
        # committed claim this test must observe.
        try:
            self.submission._job_send_to_sii()
        except error_class:
            return
        self.fail(f"{error_class.__name__} was not raised")

    def _age_send_claim(self):
        self.env.cr.execute(
            "UPDATE l10n_cl_submission_attempt "
            "SET started_at = started_at - interval '31 minutes' WHERE id = ANY(%s)",
            [self._sends().ids],
        )
        self.env.invalidate_all()

    def test_claim_is_committed_before_the_upload(self):
        events = []
        cr = self.env.cr

        @contextmanager
        def transaction():
            events.append("begin")
            try:
                with cr.savepoint():
                    yield cr
            except BaseException:
                events.append("rollback")
                raise
            events.append("commit")

        def upload(*args, **kwargs):
            events.append("upload")
            raise SiiTransportError("Timeout", dispatched=True, code="timeout")

        with (
            patch.object(modules.module, "current_test", False),
            patch.object(
                type(self.env.registry), "cursor", lambda registry, **kw: transaction()
            ),
            self._upload(upload),
        ):
            self.submission._job_send_to_sii()
        self.assertEqual(events, ["begin", "commit", "begin", "upload", "commit"])
        self.assertEqual(self.submission.state, "outcome_unknown")

    def test_lost_transaction_after_upload_never_resends(self):
        uploads = self._lose_transaction_after_upload()
        self.assertEqual(len(uploads), 1)
        # The committed claim survives: bound envelope, started attempt.
        self.assertEqual(self.submission.state, "sending")
        self.assertEqual(self._sends().state, "started")
        envelope = self.submission.transport_request_artifact_id
        self.assertEqual(self._sends().request_digest, envelope.sha256)

        with self._upload(AssertionError("second upload")) as again:
            self.submission._job_send_to_sii()
            self.submission.with_context(
                queue_job__no_delay=True
            ).action_enqueue_submission()
            self._age_send_claim()
            self.env["l10n_cl.electronic.submission"]._cron_recover_interrupted_sends()
            self.assertEqual(self.submission.state, "outcome_unknown")
            self.submission.with_context(
                queue_job__no_delay=True
            ).action_enqueue_submission()
        again.assert_not_called()
        self.assertEqual(self._sends().state, "unknown")
        self.assertEqual(self._sends().error_code, "interrupted_send")
        self.assertEqual(self.submission.document_id.fiscal_state, "outcome_unknown")
        self.assertFalse(self.submission._was_never_dispatched())
        self.assertIn("SII portal", self.submission.sii_message)

    def test_database_failure_after_upload_never_resends(self):
        lost = SiiTransportError("Timeout", dispatched=True, code="timeout")
        with (
            self._upload(lost) as upload,
            patch.object(
                type(self.submission),
                "_apply_production_outcome",
                side_effect=psycopg2.errors.SerializationFailure("conflict"),
            ),
        ):
            self._expect_loss(psycopg2.errors.SerializationFailure)
            self.submission.invalidate_recordset()
            # The worker retries the whole job after a serialization failure.
            self.submission._job_send_to_sii()
        self.assertEqual(upload.call_count, 1)
        self.assertEqual(self.submission.state, "sending")

    def test_recent_claim_is_left_to_its_worker(self):
        self._lose_transaction_after_upload()
        self.env["l10n_cl.electronic.submission"]._cron_recover_interrupted_sends()
        self.assertEqual(self.submission.state, "sending")
        self.assertEqual(self._sends().state, "started")


@tagged("post_install", "-at_install")
class TestProductionDurableSendClaim(DurableSendClaimMixin, ProductionRequestCase):
    transport = ProductionSiiTransport

    def setUp(self):
        super().setUp()
        self.submission = self.submission.sudo()


@tagged("post_install", "-at_install")
class TestCertificationDurableSendClaim(
    DurableSendClaimMixin, CertificationRequestCase
):
    transport = CertificationDocumentTransport

    def setUp(self):
        super().setUp()
        _document, self.submission, _caf = self.issue()
