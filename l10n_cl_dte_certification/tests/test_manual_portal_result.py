# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import DteCertificationTestCase


@tagged("post_install", "-at_install")
class TestDteCertificationManualPortalResult(DteCertificationTestCase):
    def setUp(self):
        super().setUp()
        self.process = self.create_certification_process()
        self.process.action_start()
        self.stage = self.process.stage_ids.sorted("sequence")[0]
        self.stage._write_internal({"state": "submitted"})
        self.requirement = self.stage.requirement_ids.filtered(
            lambda item: item.code == "assigned_set_sii_result"
        )

    def _wizard(self, **overrides):
        values = {
            "stage_id": self.stage.id,
            "requirement_id": self.requirement.id,
            "result": "accepted",
            "decision_date": "2026-11-03",
            "source": "sii_portal",
            "external_reference": "CERT-RESULT-001",
            "evidence_file": base64.b64encode(b"official SII result"),
            "filename": "result.txt",
        }
        values.update(overrides)
        return (
            self.env["l10n_cl.dte.certification.manual.result.wizard"]
            .with_user(self.certification_manager)
            .create(values)
        )

    def test_manual_result_requires_manager_and_authoritative_evidence(self):
        with self.assertRaises(ValidationError):
            self._wizard(external_reference="").action_apply()
        with self.assertRaises(ValidationError):
            wizard = (
                self.env["l10n_cl.dte.certification.manual.result.wizard"]
                .with_user(self.certification_manager)
                .new(
                    {
                        "stage_id": self.stage.id,
                        "requirement_id": self.requirement.id,
                        "result": "accepted",
                        "decision_date": "2026-11-03",
                        "source": "sii_portal",
                        "external_reference": "CERT-RESULT-001",
                        "evidence_file": "invalid-base64",
                        "filename": "result.txt",
                    }
                )
            )
            wizard.action_apply()

    def test_existing_assigned_set_result_requires_accepted_linked_envelope(self):
        self.stage._write_internal({"state": "ready"})
        self.assertFalse(self.stage.can_record_existing_sii_result)
        with self.assertRaisesRegex(
            UserError, "already accepted assigned-set envelope"
        ):
            self.stage.action_accept_manual_result()

    def test_ready_assigned_set_can_record_result_for_existing_accepted_envelope(self):
        self.stage._write_internal({"state": "ready"})
        with patch.object(
            type(self.stage), "_has_accepted_assigned_set_envelope", return_value=True
        ):
            self._wizard().action_apply()
        self.assertEqual(self.stage.state, "accepted")
        self.assertEqual(self.stage.sii_status, "accepted")
        self.assertEqual(self.process.current_stage_code, "simulation_set")
        event = self.process.event_ids.filtered(
            lambda item: item.event_code == "stage_manual_result_accepted"
        )[-1]
        self.assertEqual(event.from_state, "ready")

    def test_manual_result_keeps_origin_user_and_external_reference(self):
        self._wizard().action_apply()
        self.assertEqual(self.stage.state, "accepted")
        self.assertEqual(self.stage.external_reference, "CERT-RESULT-001")
        evidence = self.process.evidence_ids.filtered(
            lambda item: item.external_reference == "CERT-RESULT-001"
        )
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence.origin, "sii_portal")
        self.assertEqual(evidence.verification_state, "source_verified")
        self.assertEqual(evidence.verified_by, self.certification_manager)
        self.assertEqual(evidence.captured_by, self.certification_manager)
        result_requirement = self.requirement
        self.assertEqual(result_requirement.state, "accepted")
        self.assertEqual(result_requirement.evidence_ids, evidence)

    def test_manual_rejection_is_bound_to_external_result_requirement(self):
        self._wizard(
            result="rejected", notes="SII rejected the test set"
        ).action_apply()
        result_requirement = self.requirement
        evidence = result_requirement.evidence_ids
        self.assertEqual(self.stage.state, "rejected")
        self.assertEqual(result_requirement.state, "rejected")
        self.assertEqual(evidence.verification_state, "source_verified")

    def test_external_result_does_not_block_pre_submission_preflight(self):
        stage = self.process.stage_ids.filtered(
            lambda item: item.code == "issuer_registration"
        )
        stage._write_internal({"state": "in_progress"})
        stage.action_run_preflight()
        self.assertEqual(stage.state, "ready")
        self.assertFalse(stage.diagnostics)
