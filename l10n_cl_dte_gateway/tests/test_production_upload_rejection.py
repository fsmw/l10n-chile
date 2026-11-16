# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common_production_request import ProductionRequestCase
from .test_certification_transport import upload_response


@tagged("post_install", "-at_install")
class TestProductionUploadRejection(ProductionRequestCase):
    def setUp(self):
        super().setUp()
        self.envelope = self.submission._prepare_production_request()
        self.submission._write_internal({"state": "sending"})
        self.raw = (
            upload_response(status="7")
            .replace(
                b"<RUTCOMPANY>12345678-5</RUTCOMPANY>",
                f"<RUTCOMPANY>{self.company.vat}</RUTCOMPANY>".encode(),
            )
            .replace(
                b"<RUTSENDER>12345678-5</RUTSENDER>",
                f"<RUTSENDER>{self.company.dte_sii_sender_vat}</RUTSENDER>".encode(),
            )
        )
        self.response = self.create_artifact(self.document, "sii_response", self.raw)
        self.events = self.document.event_ids

    def _attempt(self, **values):
        return (
            self.env["l10n_cl.submission.attempt"]
            .sudo()
            .create(
                {
                    "submission_id": self.submission.id,
                    "operation": "send",
                    "request_digest": self.envelope.sha256,
                    "response_digest": self.response.sha256,
                    "state": "completed",
                    "completed_at": fields.Datetime.now(),
                    **values,
                }
            )
        )

    def _reject(self, document=None, response=None, **values):
        return (
            document if document is not None else self.document.sudo()
        ).action_record_transition(
            "rejected",
            "sii_upload_rejected",
            **{
                "artifact_id": (response if response is not None else self.response).id,
                "correlation_key": self.submission.idempotency_key,
                **values,
            },
        )

    def test_verified_receipt_rejects_directly_without_intermediate_event(self):
        attempt = self._attempt(
            state="started", completed_at=False, response_digest=False
        )
        attempt._complete("completed", response_digest=self.response.sha256)
        self._reject()
        events = self.document.event_ids - self.events
        self.assertEqual(len(events), 1)
        self.assertEqual((events.from_state, events.to_state), ("unknown", "rejected"))
        self.assertEqual(events.artifact_id, self.response)
        self.assertEqual(events.correlation_key, self.submission.idempotency_key)
        self.assertEqual(self.submission.state, "sending")

    def test_manager_cannot_forge_receipt_even_with_matching_audit(self):
        self._attempt()
        with self.assertRaises(UserError):
            self._reject(document=self.document.with_user(self.dte_manager))
        self.assertEqual(self.document.fiscal_state, "unknown")
        self.assertEqual(self.document.event_ids, self.events)

    def test_missing_or_wrong_correlation_cannot_use_exception(self):
        self._attempt()
        for key in (False, "another-request"):
            with self.subTest(key=key), self.assertRaises(UserError):
                self._reject(correlation_key=key)

    def test_exactly_one_completed_send_with_matching_digests_is_required(self):
        with self.assertRaises(UserError):
            self._reject()
        for values in (
            {"request_digest": "0" * 64},
            {"response_digest": "0" * 64},
            {"state": "started"},
            {"state": "unknown"},
            {"completed_at": False},
            {"operation": "query"},
        ):
            with (
                self.subTest(values=values),
                self.assertRaises(UserError),
                self.cr.savepoint(),
            ):
                self._attempt(**values)
                self._reject()
        self._attempt()
        self._attempt()
        with self.assertRaises(UserError):
            self._reject()

    def test_submission_must_be_sending_without_track_and_company_production(self):
        self._attempt()
        for values in (
            {"state": "pending"},
            {"state": "outcome_unknown"},
            {"track_id": "123"},
        ):
            with (
                self.subTest(values=values),
                self.assertRaises(UserError),
                self.cr.savepoint(),
            ):
                self.submission._write_internal(values)
                self._reject()
        self.company.sudo().write({"dte_sii_environment": "certification"})
        with self.assertRaises(UserError):
            self._reject()

    def test_status_response_cannot_exploit_new_unknown_edge(self):
        response = self.create_artifact(self.document, "status_response", self.raw)
        self._attempt(response_digest=response.sha256)
        with self.assertRaises(UserError):
            self._reject(response=response)

    def test_other_document_receipt_cannot_be_used(self):
        response = self.create_artifact(
            self.create_document(), "sii_response", self.raw
        )
        self._attempt(response_digest=response.sha256)
        with self.assertRaises(UserError):
            self._reject(response=response)

    def test_actual_receipt_is_parsed_against_bound_envelope(self):
        for raw in (
            self.raw.replace(b"<STATUS>7</STATUS>", b"<STATUS>0</STATUS>"),
            self.raw.replace(b"<RUTCOMPANY>", b"<RUTCOMPANY>1"),
            self.raw.replace(b"<RUTSENDER>", b"<RUTSENDER>1"),
            b"<invalid>",
        ):
            with (
                self.subTest(raw=raw),
                self.assertRaises(UserError),
                self.cr.savepoint(),
            ):
                response = self.create_artifact(self.document, "sii_response", raw)
                self._attempt(response_digest=response.sha256)
                self._reject(response=response)

    def test_transition_map_is_not_modified_for_other_documents_or_users(self):
        self.assertIn(
            "rejected",
            self.document.sudo()._get_transition_details("rejected")[2]["unknown"],
        )
        other = self.document.with_user(self.dte_manager)
        self.assertNotIn(
            "rejected", other._get_transition_details("rejected")[2]["unknown"]
        )

    def test_completed_rejection_cannot_create_another_event(self):
        self._attempt()
        self._reject()
        with self.assertRaises(UserError):
            self._reject()
        self.assertEqual(len(self.document.event_ids - self.events), 1)
