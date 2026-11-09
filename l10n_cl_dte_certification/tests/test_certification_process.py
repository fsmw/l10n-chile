# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import SQL

from .common import DteCertificationTestCase


@tagged("post_install", "-at_install")
class TestDteCertificationProcess(DteCertificationTestCase):
    def test_process_creates_six_ordered_stages(self):
        process = self.create_certification_process()
        self.assertEqual(len(process.stage_ids), 6)
        self.assertEqual(
            process.stage_ids.sorted("sequence").mapped("code"),
            [
                "assigned_set",
                "simulation_set",
                "information_exchange",
                "printed_samples",
                "compliance_declaration",
                "issuer_registration",
            ],
        )
        self.assertTrue(all(stage.requirement_ids for stage in process.stage_ids))
        self.assertEqual(process.current_stage_code, "assigned_set")

    def test_pending_stage_can_link_existing_sii_envelope_before_submission(self):
        process = self.create_certification_process()
        stage = process.stage_ids.filtered(lambda item: item.code == "assigned_set")
        stage.with_user(self.certification_manager).write(
            {"sii_envelope_submission_ids": [Command.clear()]}
        )
        self.assertEqual(stage.state, "pending")

    def test_process_start_requires_accepted_sii_postulation_evidence(self):
        process = self.create_certification_process(record_postulation=False)
        with self.assertRaisesRegex(UserError, "accepted postulation"):
            process.action_start()

    def test_assigned_set_cycle_does_not_ask_for_a_second_postulation(self):
        process = self.create_certification_process(
            record_postulation=False,
            process_mode="assigned_test_set",
        )
        requirements = process._entry_requirements()
        self.assertEqual(set(requirements.mapped("state")), {"not_applicable"})
        with self.assertRaisesRegex(UserError, "Link the SII-assigned test set"):
            process.action_start_assigned_test_set_cycle()
        self.assertEqual(process.process_mode, "assigned_test_set")

    def test_basic_set_cycle_button_starts_without_postulation_evidence(self):
        process = self.create_certification_process(
            record_postulation=False,
            process_mode="new_postulation",
            sii_application_reference=False,
        )
        assigned_stage = process.stage_ids.filtered(
            lambda item: item.code == "assigned_set"
        )
        with patch.object(
            type(process),
            "_validate_assigned_test_set_entry",
            return_value=assigned_stage,
        ):
            process.action_start_assigned_test_set_cycle()
        self.assertEqual(process.process_mode, "assigned_test_set")
        self.assertTrue(process.started_at)
        self.assertEqual(process.current_stage_code, "assigned_set")

    def test_already_authorized_taxpayer_can_start_with_authorization_evidence(self):
        process = self.create_certification_process(
            record_postulation=False, process_mode="authorized_taxpayer"
        )
        with self.assertRaisesRegex(UserError, "existing SII authorization"):
            process.action_start()

        wizard = (
            self.env["l10n_cl.dte.certification.authorization.wizard"]
            .with_user(self.certification_manager)
            .create(
                {
                    "process_id": process.id,
                    "evidence_date": "2026-11-04",
                    "source_url": "https://www.sii.cl/factura_electronica/",
                    "evidence_file": base64.b64encode(
                        b"Synthetic existing SII authorization evidence"
                    ),
                    "filename": "existing-authorization.txt",
                }
            )
        )
        wizard.action_apply()

        requirement = process._entry_requirements().filtered(
            lambda item: item.code == "existing_authorization"
        )
        postulation = process._entry_requirements().filtered(
            lambda item: item.code == "accepted_postulation"
        )
        self.assertEqual(requirement.state, "accepted")
        self.assertEqual(requirement.evidence_ids.verification_state, "source_verified")
        self.assertEqual(postulation.state, "not_applicable")
        self.assertFalse(process.sii_application_reference)
        process.action_start()
        assigned_stage = process.stage_ids.filtered(
            lambda item: item.code == "assigned_set"
        )
        assigned_stage.with_user(self.certification_manager).action_begin()
        self.assertEqual(assigned_stage.state, "in_progress")

    def test_existing_authorization_requires_reference_or_source_url(self):
        process = self.create_certification_process(
            record_postulation=False, process_mode="authorized_taxpayer"
        )
        wizard = (
            self.env["l10n_cl.dte.certification.authorization.wizard"]
            .with_user(self.certification_manager)
            .create(
                {
                    "process_id": process.id,
                    "evidence_date": "2026-11-04",
                    "evidence_file": base64.b64encode(b"authorization evidence"),
                    "filename": "authorization.txt",
                }
            )
        )
        with self.assertRaisesRegex(ValidationError, "SII reference"):
            wizard.action_apply()

    def test_authorized_taxpayer_cannot_store_a_new_postulation_reference(self):
        with self.assertRaises(ValidationError):
            self.create_certification_process(
                record_postulation=False,
                process_mode="authorized_taxpayer",
                sii_application_reference="NOT-A-NEW-POSTULATION",
            )

    def test_draft_process_mode_switch_updates_the_entry_requirement(self):
        process = self.create_certification_process(record_postulation=False)
        process.write(
            {
                "sii_application_reference": False,
                "process_mode": "authorized_taxpayer",
            }
        )
        requirements = process._entry_requirements()
        self.assertEqual(
            requirements.filtered(
                lambda item: item.code == "existing_authorization"
            ).applicability,
            "required",
        )
        self.assertEqual(
            requirements.filtered(
                lambda item: item.code == "accepted_postulation"
            ).applicability,
            "not_applicable",
        )

    def test_postulation_wizard_records_source_evidence_before_process_start(self):
        process = self.create_certification_process(record_postulation=False)
        wizard = (
            self.env["l10n_cl.dte.certification.postulation.wizard"]
            .with_user(self.certification_manager)
            .create(
                {
                    "process_id": process.id,
                    "application_reference": "SII-POSTULATION-001",
                    "accepted_at": "2026-11-03",
                    "evidence_file": base64.b64encode(b"synthetic SII acceptance"),
                    "filename": "postulation-acceptance.txt",
                }
            )
        )
        wizard.action_apply()
        stage = process.stage_ids.filtered(lambda item: item.code == "assigned_set")
        requirement = stage.requirement_ids.filtered(
            lambda item: item.code == "accepted_postulation"
        )
        evidence = requirement.evidence_ids
        self.assertEqual(process.sii_application_reference, "SII-POSTULATION-001")
        self.assertEqual(requirement.state, "accepted")
        self.assertEqual(evidence.evidence_type, "postulation_acceptance")
        self.assertEqual(evidence.origin, "sii_portal")
        self.assertEqual(evidence.verification_state, "source_verified")
        process.action_start()
        self.assertTrue(process.started_at)

    def test_postulation_acceptance_can_be_recorded_without_portal_reference(self):
        process = self.create_certification_process(record_postulation=False)
        wizard_model = self.env[
            "l10n_cl.dte.certification.postulation.wizard"
        ].with_user(self.certification_manager)
        wizard = wizard_model.create(
            {
                "process_id": process.id,
                "accepted_at": "2026-11-03",
                "evidence_file": base64.b64encode(b"synthetic SII acceptance"),
                "filename": "postulation-acceptance.txt",
            }
        )
        wizard.action_apply()
        self.assertFalse(process.sii_application_reference)
        requirement = process._entry_requirements().filtered(
            lambda item: item.code == "accepted_postulation"
        )
        evidence = requirement.evidence_ids
        self.assertEqual(
            evidence.source_url,
            "https://maullin.sii.cl/cvc_cgi/dte/pe_avance5",
        )
        process.action_start()
        self.assertTrue(process.started_at)

    def test_postulation_acceptance_still_requires_evidence_file(self):
        process = self.create_certification_process(record_postulation=False)
        wizard_model = self.env[
            "l10n_cl.dte.certification.postulation.wizard"
        ].with_user(self.certification_manager)
        with self.assertRaises(ValidationError):
            wizard_model.create(
                {
                    "process_id": process.id,
                    "accepted_at": "2026-11-03",
                    "evidence_file": False,
                    "filename": "postulation-acceptance.txt",
                }
            ).action_apply()

    def test_setmail_cannot_be_used_as_postulation_acceptance_evidence(self):
        process = self.create_certification_process(record_postulation=False)
        wizard = (
            self.env["l10n_cl.dte.certification.postulation.wizard"]
            .with_user(self.certification_manager)
            .create(
                {
                    "process_id": process.id,
                    "evidence_file": base64.b64encode(b"SOK - SET DE PRUEBA CORRECTO"),
                    "filename": "SETMAIL0005165992.txt",
                }
            )
        )
        with self.assertRaisesRegex(ValidationError, "assigned test-set result"):
            wizard.action_apply()

    def test_supplemental_postulation_evidence_supersedes_misfiled_setmail(self):
        process = self.create_certification_process(record_postulation=False)
        stage = process.stage_ids.filtered(lambda item: item.code == "assigned_set")
        requirement = process._entry_requirements().filtered(
            lambda item: item.code == "accepted_postulation"
        )
        setmail_content = b"SOK - SET DE PRUEBA CORRECTO"
        attachment = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": "SETMAIL0005165992.txt",
                    "type": "binary",
                    "datas": base64.b64encode(setmail_content),
                    "res_model": process._name,
                    "res_id": process.id,
                    "public": False,
                }
            )
        )
        prior_evidence = (
            self.env["l10n_cl.dte.certification.evidence"]
            .with_user(self.certification_manager)
            ._create_from_source(
                {
                    "stage_id": stage.id,
                    "requirement_id": requirement.id,
                    "name": "SETMAIL0005165992.txt",
                    "evidence_type": "postulation_acceptance",
                    "origin": "sii_portal",
                    "attachment_id": attachment.id,
                    "source_url": "https://maullin.sii.cl/cvc_cgi/dte/pe_avance5",
                }
            )
        )
        prior_evidence.action_mark_source_verified()
        requirement._record_external_result(prior_evidence, accepted=True)
        wizard_model = self.env[
            "l10n_cl.dte.certification.postulation.wizard"
        ].with_user(self.certification_manager)
        second = wizard_model.create(
            {
                "process_id": process.id,
                "evidence_file": base64.b64encode(b"corrected SII status screenshot"),
                "filename": "status-correction.png",
                "notes": "Adding the SII status screenshot for audit clarity.",
            }
        )
        second.action_apply()
        self.assertEqual(requirement.state, "accepted")
        self.assertEqual(len(requirement.evidence_ids), 2)
        self.assertEqual(prior_evidence.verification_state, "rejected")
        self.assertIn("retained for audit", prior_evidence.notes)
        self.assertTrue(second.is_supplemental)

    def test_legacy_started_dossier_backfills_pending_postulation_gate(self):
        process = self.create_certification_process(record_postulation=False)
        stage = process.stage_ids.filtered(lambda item: item.code == "assigned_set")
        requirement = stage.requirement_ids.filtered(
            lambda item: item.code == "accepted_postulation"
        )
        self.assertTrue(requirement)
        requirement_model = self.env["l10n_cl.dte.certification.process.requirement"]
        self.env.cr.execute(
            SQL(
                "DELETE FROM %s WHERE id = %s",
                SQL.identifier(requirement_model._table),
                requirement.id,
            )
        )
        requirement_model.invalidate_model()
        stage.invalidate_recordset(["requirement_ids"])
        process.invalidate_recordset(["stage_ids", "current_stage_code"])
        process._write_internal({"started_at": "2026-11-01 12:00:00"})

        with self.assertRaisesRegex(UserError, "accepted postulation"):
            stage.with_user(self.certification_manager).action_begin()
        migrated_requirement = process._ensure_postulation_requirement()
        self.assertEqual(migrated_requirement.state, "pending")
        with self.assertRaisesRegex(UserError, "accepted postulation"):
            process.action_start()

        wizard = (
            self.env["l10n_cl.dte.certification.postulation.wizard"]
            .with_user(self.certification_manager)
            .create(
                {
                    "process_id": process.id,
                    "application_reference": "SII-LEGACY-POSTULATION",
                    "accepted_at": "2026-11-03",
                    "evidence_file": base64.b64encode(b"synthetic SII acceptance"),
                    "filename": "legacy-postulation-acceptance.txt",
                }
            )
        )
        wizard.action_apply()
        self.assertEqual(migrated_requirement.state, "accepted")
        self.assertIn(
            "legacy_postulation_acceptance_recorded",
            process.event_ids.mapped("event_code"),
        )
        stage.with_user(self.certification_manager).action_begin()
        self.assertEqual(stage.state, "in_progress")

    def test_checklist_covers_certification_books_and_compliance_controls(self):
        process = self.create_certification_process()
        assigned_codes = set(
            process.stage_ids.filtered(
                lambda stage: stage.code == "assigned_set"
            ).requirement_ids.mapped("code")
        )
        compliance_codes = set(
            process.stage_ids.filtered(
                lambda stage: stage.code == "compliance_declaration"
            ).requirement_ids.mapped("code")
        )
        self.assertIn("assigned_set_iev", assigned_codes)
        self.assertIn("assigned_set_iec", assigned_codes)
        self.assertTrue(
            {
                "compliance_caf_access_control",
                "compliance_unique_foliation",
                "compliance_backups",
                "compliance_sii_outbound",
                "compliance_document_exchange",
                "compliance_sii_reconciliation",
                "compliance_contingency_management",
            }.issubset(compliance_codes)
        )

    def test_operational_control_requires_owner_procedure_and_review(self):
        process = self.create_certification_process()
        process.action_start()
        stage = process.stage_ids.filtered(
            lambda item: item.code == "compliance_declaration"
        )
        requirement = stage.requirement_ids.filtered(
            lambda item: item.code == "compliance_backups"
        )
        self.assertTrue(requirement.requires_operational_control_review)
        wizard = (
            self.env["l10n_cl.dte.certification.evidence.wizard"]
            .with_user(self.certification_manager)
            .create(
                {
                    "stage_id": stage.id,
                    "requirement_id": requirement.id,
                    "name": "Synthetic backup procedure evidence",
                    "evidence_type": "operator_note",
                    "origin": "operator_imported",
                    "upload_file": base64.b64encode(b"synthetic evidence"),
                    "filename": "backup-procedure.txt",
                }
            )
        )
        wizard.action_create_evidence()
        with self.assertRaises(ValidationError):
            requirement.with_user(self.certification_manager).action_mark_ready()
        requirement.with_user(self.certification_manager).write(
            {
                "control_owner_id": self.certification_manager.id,
                "procedure_version": "TEST-1",
                "last_review_date": "2026-11-03",
                "control_result": "passed",
            }
        )
        requirement.with_user(self.certification_manager).action_mark_ready()
        self.assertEqual(requirement.state, "ready")

    def test_process_cannot_skip_unaccepted_stage(self):
        process = self.create_certification_process()
        process.action_start()
        later_stage = process.stage_ids.filtered(
            lambda stage: stage.code == "simulation_set"
        )
        with self.assertRaises(UserError):
            later_stage.with_user(self.certification_manager).action_begin()

    def test_demo_and_production_cannot_satisfy_certification(self):
        company = self.company.with_user(self.certification_manager)
        for environment in ("demo", "production"):
            with self.subTest(environment=environment):
                company.write({"dte_sii_environment": environment})
                with self.assertRaises(UserError):
                    self.create_certification_process()

    def test_boleta_certification_scope_rejects_invoice_types(self):
        process = self.create_certification_process(certification_scope="boleta")
        with self.assertRaises(ValidationError):
            process._validate_scope_codes("boleta", {"39", "33"})

    def test_invoice_certification_scope_rejects_boleta_types(self):
        process = self.create_certification_process(certification_scope="dte")
        with self.assertRaises(ValidationError):
            process._validate_scope_codes("dte", {"33", "39"})

    def test_boleta_scope_accepts_boleta_document_types(self):
        process = self.create_certification_process(certification_scope="boleta")
        self.assertIsNone(process._validate_scope_codes("boleta", {"39", "41"}))

    def test_completion_requires_issuer_registration_evidence(self):
        process = self.create_certification_process()
        process.action_start()
        process._write_internal(
            {
                "resolution_number": "TEST-RESOLUTION",
                "resolution_date": "2026-01-01",
                "authorized_document_types": ["33"],
            }
        )
        process.stage_ids._write_internal({"state": "accepted"})
        process.invalidate_recordset(["state"])
        self.assertNotEqual(process.state, "completed")
