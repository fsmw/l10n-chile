# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_MAX_RECEIPT_BYTES = 20 * 1024 * 1024


class DteCertificationSubmissionWizard(models.TransientModel):
    _name = "l10n_cl.dte.certification.submission.wizard"
    _description = "Record SII Certification Portal Submission"

    stage_id = fields.Many2one(
        "l10n_cl.dte.certification.process.stage",
        required=True,
        ondelete="cascade",
    )
    expected_payload_digest = fields.Char(
        compute="_compute_expected_payload_digest",
        string="Signed Envelope SHA-256",
    )
    confirms_exact_payload = fields.Boolean(
        string="I uploaded this exact signed envelope to the SII portal",
        required=True,
    )
    external_reference = fields.Char(required=True, string="SII TrackID / Reference")
    submitted_at = fields.Datetime(required=True, default=fields.Datetime.now)
    receipt_file = fields.Binary(required=True, string="SII Upload Receipt")
    filename = fields.Char(required=True)
    notes = fields.Text()

    @api.depends("stage_id", "stage_id.certification_run_ids.envelope_digest")
    def _compute_expected_payload_digest(self):
        for wizard in self:
            runs = wizard.stage_id.certification_run_ids
            wizard.expected_payload_digest = (
                runs.envelope_digest if len(runs) == 1 else False
            )

    def action_apply(self):
        """Record the operator-attested portal upload and exact payload hash."""
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(
                _("Only a DTE manager can record an SII Certification upload.")
            )
        stage = self.stage_id
        stage._check_stage_access()
        if stage.state != "ready" or stage.code not in {
            "assigned_set",
            "simulation_set",
        }:
            raise UserError(
                _("Only a ready assigned-set or simulation stage can be submitted.")
            )
        runs = stage.certification_run_ids
        if len(runs) != 1:
            raise ValidationError(_("Exactly one execution must be linked."))
        run = runs
        if run.state != "exported" or run.is_partial or not run.envelope_file:
            raise ValidationError(
                _("The linked execution must contain a complete exported envelope.")
            )
        digest, receipt = self._validate_submission_material(
            run.envelope_file,
            run.envelope_digest,
            self.confirms_exact_payload,
            self.external_reference,
            self.receipt_file,
        )

        attachment = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": self.filename,
                    "type": "binary",
                    "datas": self.receipt_file,
                    "mimetype": "application/octet-stream",
                    "res_model": stage.process_id._name,
                    "res_id": stage.process_id.id,
                    "public": False,
                }
            )
        )
        evidence = self.env["l10n_cl.dte.certification.evidence"]._create_from_source(
            {
                "stage_id": stage.id,
                "name": self.filename,
                "evidence_type": "sii_upload_receipt",
                "origin": "sii_portal",
                "attachment_id": attachment.id,
                "external_reference": self.external_reference.strip(),
                "notes": self.notes or False,
            }
        )
        evidence.action_mark_source_verified()
        stage._write_internal(
            {
                "state": "submitted",
                "submitted_at": self.submitted_at,
                "external_reference": self.external_reference.strip(),
                "submission_channel": "sii_portal",
                "submitted_payload_digest": digest,
                "sii_status": "portal_upload_recorded",
            }
        )
        run._write_internal({"reportability_state": "submitted"})
        stage.process_id._record_event(
            "stage_manual_portal_upload_recorded",
            stage=stage,
            from_state="ready",
            to_state="submitted",
            details={
                "external_reference": self.external_reference.strip(),
                "payload_digest": digest,
                "receipt_evidence_id": evidence.id,
                "receipt_sha256": evidence.sha256,
                "submitted_at": fields.Datetime.to_string(self.submitted_at),
            },
        )
        return {"type": "ir.actions.act_window_close"}

    @staticmethod
    def _validate_submission_material(
        envelope_file, expected_digest, confirmed, external_reference, receipt_file
    ):
        try:
            envelope = base64.b64decode(envelope_file, validate=True)
            receipt = base64.b64decode(receipt_file, validate=True)
        except (TypeError, ValueError) as error:
            raise ValidationError(
                _("The envelope or SII receipt has invalid base64 data.")
            ) from error
        digest = hashlib.sha256(envelope).hexdigest()
        if digest != expected_digest:
            raise ValidationError(
                _("The exported envelope no longer matches its recorded SHA-256.")
            )
        if not confirmed:
            raise ValidationError(
                _("Confirm that the exact signed envelope was uploaded to SII.")
            )
        if not (external_reference or "").strip():
            raise ValidationError(_("Enter the TrackID or SII portal reference."))
        if not receipt or len(receipt) > _MAX_RECEIPT_BYTES:
            raise ValidationError(
                _("The SII receipt must be between 1 byte and 20 MiB.")
            )
        return digest, receipt
