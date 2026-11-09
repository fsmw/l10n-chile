# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_MAX_EVIDENCE_BYTES = 20 * 1024 * 1024
_STAGE_EVIDENCE_TYPES = {
    "printed_samples": "printed_sample_pdf",
    "compliance_declaration": "compliance_declaration",
    "issuer_registration": "sii_upload_receipt",
}


class DteCertificationStageSubmissionWizard(models.TransientModel):
    _name = "l10n_cl.dte.certification.stage.submission.wizard"
    _description = "Record SII Certification Stage Portal Action"

    stage_id = fields.Many2one(
        "l10n_cl.dte.certification.process.stage",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    confirms_operation = fields.Boolean(
        string="I completed this operation in the SII portal",
        required=True,
    )
    external_reference = fields.Char(required=True)
    submitted_at = fields.Datetime(required=True, default=fields.Datetime.now)
    evidence_file = fields.Binary(required=True)
    filename = fields.Char(required=True)
    notes = fields.Text()

    def action_apply(self):
        """Capture an operator-attested portal action without implying approval."""
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(
                _("Only a DTE manager can record a Certification portal action.")
            )
        stage = self.stage_id
        stage._check_stage_access()
        expected_type = _STAGE_EVIDENCE_TYPES.get(stage.code)
        if not expected_type:
            raise UserError(
                _("This stage does not use the manual portal action workflow.")
            )
        if stage.state != "ready":
            raise UserError(_("Run and pass the stage preflight before submitting."))
        external_reference = (self.external_reference or "").strip()
        if not external_reference:
            raise ValidationError(_("Enter the SII portal reference."))
        if not self.confirms_operation:
            raise ValidationError(
                _("Confirm that you completed the operation in the SII portal.")
            )
        try:
            content = base64.b64decode(self.evidence_file, validate=True)
        except (TypeError, ValueError) as error:
            raise ValidationError(
                _("The portal evidence is not valid base64.")
            ) from error
        if not content or len(content) > _MAX_EVIDENCE_BYTES:
            raise ValidationError(_("Attach non-empty portal evidence up to 20 MiB."))
        if stage.code == "printed_samples":
            package = stage.report_sample_package_id
            if not package or package.state != "generated":
                raise ValidationError(
                    _("Link a generated print-sample package before submitting.")
                )
            self._validate_print_package(package, content, stage.company_id)
        attachment = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": self.filename,
                    "type": "binary",
                    "datas": self.evidence_file,
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
                "evidence_type": expected_type,
                "origin": "sii_portal",
                "attachment_id": attachment.id,
                "external_reference": external_reference,
                "notes": self.notes or False,
            }
        )
        evidence.action_mark_source_verified()
        digest = hashlib.sha256(content).hexdigest()
        stage._write_internal(
            {
                "state": "submitted",
                "submitted_at": self.submitted_at,
                "external_reference": external_reference,
                "submission_channel": "sii_portal",
                "submitted_payload_digest": digest,
                "sii_status": "portal_action_recorded",
            }
        )
        stage.process_id._record_event(
            "stage_manual_portal_action_recorded",
            stage=stage,
            from_state="ready",
            to_state="submitted",
            details={
                "external_reference": external_reference,
                "payload_digest": digest,
                "evidence_id": evidence.id,
                "evidence_sha256": evidence.sha256,
                "evidence_type": expected_type,
                "submitted_at": fields.Datetime.to_string(self.submitted_at),
            },
        )
        return {"type": "ir.actions.act_window_close"}

    @staticmethod
    def _validate_print_package(package, content, company):
        try:
            package_content = base64.b64decode(package.pdf_content, validate=True)
        except (TypeError, ValueError) as error:
            raise ValidationError(
                _("The generated print-sample package is invalid.")
            ) from error
        if (
            package.company_id != company
            or hashlib.sha256(package_content).hexdigest() != package.pdf_sha256
            or content != package_content
        ):
            raise ValidationError(
                _(
                    "Attach the exact generated print-sample package linked "
                    "to this stage."
                )
            )
