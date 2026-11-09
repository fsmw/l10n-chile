# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
from types import SimpleNamespace

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_certification.wizards import (
    certification_stage_submission_wizard,
)

from .common import DteCertificationTestCase

DteCertificationStageSubmissionWizard = (
    certification_stage_submission_wizard.DteCertificationStageSubmissionWizard
)


@tagged("post_install", "-at_install")
class TestDteCertificationStageSubmission(DteCertificationTestCase):
    def test_print_sample_submission_requires_exact_generated_package(self):
        content = b"synthetic generated PDF package"
        package = SimpleNamespace(
            pdf_content=base64.b64encode(content),
            pdf_sha256=hashlib.sha256(content).hexdigest(),
            company_id=self.company,
        )
        DteCertificationStageSubmissionWizard._validate_print_package(
            package, content, self.company
        )
        with self.assertRaisesRegex(ValidationError, "exact generated print-sample"):
            DteCertificationStageSubmissionWizard._validate_print_package(
                package, b"different PDF", self.company
            )

    def test_compliance_portal_action_is_recorded_without_accepting_stage(self):
        process = self.create_certification_process()
        process.action_start()
        stage = process.stage_ids.filtered(
            lambda item: item.code == "compliance_declaration"
        )
        stage._write_internal({"state": "ready"})
        action = stage.with_user(self.certification_manager).action_submit()
        self.assertEqual(
            action["res_model"],
            "l10n_cl.dte.certification.stage.submission.wizard",
        )
        wizard = (
            self.env["l10n_cl.dte.certification.stage.submission.wizard"]
            .with_user(self.certification_manager)
            .create(
                {
                    "stage_id": stage.id,
                    "confirms_operation": True,
                    "external_reference": "SII-DECLARATION-001",
                    "submitted_at": "2026-11-03 12:00:00",
                    "evidence_file": base64.b64encode(b"synthetic portal receipt"),
                    "filename": "declaration-receipt.txt",
                }
            )
        )
        wizard.action_apply()
        self.assertEqual(stage.state, "submitted")
        self.assertEqual(stage.sii_status, "portal_action_recorded")
        self.assertEqual(stage.external_reference, "SII-DECLARATION-001")
        self.assertEqual(stage.evidence_ids[-1].verification_state, "source_verified")
