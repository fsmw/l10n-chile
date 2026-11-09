# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_certification.wizards import (
    certification_submission_wizard,
)

from .common import DteCertificationTestCase

DteCertificationSubmissionWizard = (
    certification_submission_wizard.DteCertificationSubmissionWizard
)


@tagged("post_install", "-at_install")
class TestDteCertificationSubmission(DteCertificationTestCase):
    def test_manual_submission_requires_the_exact_exported_payload(self):
        envelope = b"signed SII envelope"
        receipt = b"SII portal receipt"
        digest = hashlib.sha256(envelope).hexdigest()
        actual_digest, decoded_receipt = (
            DteCertificationSubmissionWizard._validate_submission_material(
                base64.b64encode(envelope),
                digest,
                True,
                "TRACK-123",
                base64.b64encode(receipt),
            )
        )
        self.assertEqual(actual_digest, digest)
        self.assertEqual(decoded_receipt, receipt)

    def test_manual_submission_rejects_mismatched_digest(self):
        with self.assertRaisesRegex(ValidationError, "no longer matches its recorded"):
            DteCertificationSubmissionWizard._validate_submission_material(
                base64.b64encode(b"different envelope"),
                "0" * 64,
                True,
                "TRACK-123",
                base64.b64encode(b"SII portal receipt"),
            )

    def test_manual_submission_requires_operator_confirmation_and_trackid(self):
        values = (
            base64.b64encode(b"signed SII envelope"),
            hashlib.sha256(b"signed SII envelope").hexdigest(),
        )
        with self.assertRaisesRegex(ValidationError, "Confirm that the exact"):
            DteCertificationSubmissionWizard._validate_submission_material(
                *values,
                False,
                "TRACK-123",
                base64.b64encode(b"receipt"),
            )
        with self.assertRaisesRegex(ValidationError, "TrackID"):
            DteCertificationSubmissionWizard._validate_submission_material(
                *values,
                True,
                " ",
                base64.b64encode(b"receipt"),
            )

    def test_manual_submission_requires_an_actual_receipt(self):
        envelope = b"signed SII envelope"
        with self.assertRaisesRegex(ValidationError, "receipt must be between"):
            DteCertificationSubmissionWizard._validate_submission_material(
                base64.b64encode(envelope),
                hashlib.sha256(envelope).hexdigest(),
                True,
                "TRACK-123",
                base64.b64encode(b""),
            )

    def test_action_submit_does_not_mark_stage_submitted_without_a_run(self):
        process = self.create_certification_process()
        stage = process.stage_ids.sorted("sequence")[:1]
        stage._write_internal({"state": "ready"})
        with self.assertRaisesRegex(ValidationError, "Exactly one execution"):
            stage.action_submit()
        self.assertEqual(stage.state, "ready")
