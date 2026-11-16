# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from ..services.production_gateway import ProductionSiiGateway
from .common_production_request import ProductionRequestCase

BINDING = "transport_request_artifact_id"


@tagged("post_install", "-at_install")
class TestProductionBinding(ProductionRequestCase):
    def setUp(self):
        super().setUp()
        self.envelope = ProductionSiiGateway().prepare_document(
            self.company, "production", self.signed
        )
        self.artifact = self.create_artifact(
            self.document, "sii_request", self.envelope
        )

    def test_private_binding_preserves_evidence_and_workflow(self):
        self.submission._write_internal({BINDING: self.artifact.id})
        self.assertEqual(self.submission[BINDING], self.artifact)
        self.assertEqual(base64.b64decode(self.artifact.content), self.envelope)
        self.assertEqual(base64.b64decode(self.original.content), self.signed)
        self.assertEqual(self.submission.state, "pending")
        self.assertFalse(self.submission.attempt_ids)
        self.assertFalse(self.submission._fields[BINDING].copy)
        for value in (False, self.artifact.id, self.original.id):
            with self.subTest(value=value), self.assertRaises(UserError):
                self.submission._write_internal({BINDING: value})
        self.submission._write_internal({"state": "submitted", "track_id": "123"})
        self.assertEqual(self.submission[BINDING], self.artifact)

    def test_client_injection_and_context_bypasses_are_rejected(self):
        values = {
            "document_id": self.create_document().id,
            "idempotency_key": "injected",
        }
        for model, vals in (
            (self.submission, {**values, BINDING: self.artifact.id}),
            (
                self.submission.with_context(
                    default_transport_request_artifact_id=self.artifact.id
                ),
                values,
            ),
        ):
            with self.assertRaises(UserError):
                model.create(vals)
        for actor in (self.dte_manager, self.dte_user):
            record = self.submission.with_user(actor).with_context(dte_internal=True)
            with self.assertRaises(UserError):
                record.write({BINDING: self.artifact.id})
            with self.assertRaises(AccessError):
                record._write_internal({BINDING: self.artifact.id})
        with self.assertRaises(UserError):
            self.submission._write_internal(
                {BINDING: self.artifact.id, "state": "accepted"}
            )
        self.assertFalse(self.submission[BINDING])

    def test_only_own_intact_request_evidence_can_be_bound(self):
        for artifact in (
            self.create_artifact(self.document, "sii_response", self.envelope),
            self.create_artifact(self.create_document(), "sii_request", self.envelope),
        ):
            with self.subTest(artifact=artifact.id), self.assertRaises(ValidationError):
                self.submission._write_internal({BINDING: artifact.id})
            self.assertFalse(self.submission[BINDING])
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.cr.execute(
                "UPDATE l10n_cl_electronic_artifact SET sha256 = %s WHERE id = %s",
                ["bad", self.artifact.id],
            )
            self.artifact.invalidate_recordset()
            self.submission._write_internal({BINDING: self.artifact.id})
        self.assertFalse(self.submission[BINDING])

    def test_binding_is_limited_to_pending_production_before_send(self):
        for state in (
            "sending",
            "submitted",
            "accepted",
            "rejected",
            "outcome_unknown",
            "error",
        ):
            self.submission._write_internal({"state": state})
            with self.subTest(state=state), self.assertRaises(UserError):
                self.submission._write_internal({BINDING: self.artifact.id})
        self.submission._write_internal({"state": "pending"})
        self.env["l10n_cl.submission.attempt"].create(
            {
                "submission_id": self.submission.id,
                "operation": "send",
                "request_digest": self.artifact.sha256,
            }
        )
        with self.assertRaises(UserError):
            self.submission._write_internal({BINDING: self.artifact.id})
        self.assertFalse(self.submission[BINDING])

    def test_nullable_legacy_rows_and_unique_envelope(self):
        other = self.submission.create(
            {"document_id": self.create_document().id, "idempotency_key": "other"}
        )
        self.assertFalse(other[BINDING])
        self.assertFalse(self.submission[BINDING])
        self.submission._write_internal({BINDING: self.artifact.id})
        copied = self.submission.copy(
            {"document_id": self.create_document().id, "idempotency_key": "copied"}
        )
        self.assertFalse(copied[BINDING])
        with (
            self.assertRaises(IntegrityError),
            mute_logger("odoo.sql_db"),
            self.cr.savepoint(),
        ):
            self.cr.execute(
                "UPDATE l10n_cl_electronic_submission "
                "SET transport_request_artifact_id = %s WHERE id = %s",
                [self.artifact.id, other.id],
            )
        self.company.sudo().write({"dte_sii_environment": "demo"})
        with self.assertRaises(UserError):
            other._write_internal({BINDING: self.artifact.id})
        demo = self.submission.create(
            {"document_id": self.create_document().id, "idempotency_key": "demo"}
        )
        self.assertFalse(demo[BINDING])
        with self.assertRaises(UserError):
            demo._write_internal({BINDING: self.artifact.id})
