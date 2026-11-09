# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import DteCertificationTestCase


@tagged("post_install", "-at_install")
class TestDteCertificationEvidence(DteCertificationTestCase):
    def setUp(self):
        super().setUp()
        self.process = self.create_certification_process()
        self.process.action_start()
        self.stage = self.process.stage_ids.sorted("sequence")[0]

    def _create_evidence(self, content=b"SII portal response"):
        previous_evidence = self.process.evidence_ids
        wizard = (
            self.env["l10n_cl.dte.certification.evidence.wizard"]
            .with_user(self.certification_manager)
            .create(
                {
                    "stage_id": self.stage.id,
                    "name": "SII evidence",
                    "evidence_type": "sii_processing_result",
                    "origin": "sii_portal",
                    "upload_file": base64.b64encode(content),
                    "filename": "result.txt",
                    "external_reference": "portal-case-1",
                }
            )
        )
        wizard.action_create_evidence()
        return self.process.evidence_ids - previous_evidence

    def test_evidence_digest_is_computed_from_attachment(self):
        content = b"SII portal response fixture"
        evidence = self._create_evidence(content)
        self.assertEqual(evidence.sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(evidence.captured_by, self.certification_manager)
        self.assertEqual(evidence.origin, "sii_portal")

    def test_verified_evidence_is_append_only(self):
        evidence = self._create_evidence()
        with self.assertRaises(UserError):
            evidence.with_user(self.certification_manager).write({"sha256": "0" * 64})
        with self.assertRaises(UserError):
            evidence.with_user(self.certification_manager).unlink()

    def test_requirement_from_another_stage_is_rejected(self):
        wizard = self.env["l10n_cl.dte.certification.evidence.wizard"].with_user(
            self.certification_manager
        )
        wizard = wizard.create(
            {
                "stage_id": self.stage.id,
                "requirement_id": self.process.stage_ids.filtered(
                    lambda item: item != self.stage
                )[0]
                .requirement_ids[0]
                .id,
                "name": "Wrong stage requirement",
                "evidence_type": "operator_note",
                "origin": "operator_imported",
                "upload_file": base64.b64encode(b"note"),
                "filename": "note.txt",
            }
        )
        with self.assertRaises(ValidationError):
            wizard.action_create_evidence()

    def test_secret_material_is_rejected_as_evidence(self):
        with self.assertRaises(ValidationError):
            self.env["l10n_cl.dte.certification.evidence"]._check_no_secret_material(
                b"-----BEGIN PRIVATE KEY----- secret",
                "evidence.txt",
            )
