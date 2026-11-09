# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_MAX_EVIDENCE_BYTES = 20 * 1024 * 1024


class DteCertificationAuthorizationWizard(models.TransientModel):
    _name = "l10n_cl.dte.certification.authorization.wizard"
    _description = "Record Existing SII Issuer Authorization"

    process_id = fields.Many2one(
        "l10n_cl.dte.certification.process",
        string="Certification Process",
        required=True,
        ondelete="cascade",
    )
    evidence_date = fields.Date(required=True, default=fields.Date.context_today)
    evidence_file = fields.Binary(string="Authorization Evidence", required=True)
    filename = fields.Char(string="File Name", required=True)
    external_reference = fields.Char(string="SII Reference")
    source_url = fields.Char(string="SII Source URL")
    notes = fields.Text()

    def action_apply(self):
        """Capture operator-reviewed evidence of an existing SII authorization."""
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(
                _("Only a DTE manager can record existing SII authorization.")
            )
        process = self.process_id
        process._check_process_access()
        if process.process_mode != "authorized_taxpayer":
            raise UserError(
                _("This evidence is only valid for an already-authorized taxpayer.")
            )
        if process.started_at:
            raise UserError(
                _("Existing-authorization evidence must be recorded before starting.")
            )
        reference = (self.external_reference or "").strip() or False
        source_url = (self.source_url or "").strip() or False
        if not reference and not source_url:
            raise ValidationError(
                _("Provide an SII reference or the URL of the authoritative source.")
            )
        try:
            content = base64.b64decode(self.evidence_file, validate=True)
        except (TypeError, ValueError) as error:
            raise ValidationError(
                _("The authorization evidence is not valid base64.")
            ) from error
        if not content or len(content) > _MAX_EVIDENCE_BYTES:
            raise ValidationError(
                _("Attach non-empty authorization evidence up to 20 MiB.")
            )
        stage = process.stage_ids.filtered(lambda item: item.code == "assigned_set")
        requirement = process._entry_requirements().filtered(
            lambda item: item.code == "existing_authorization"
        )
        if len(stage) != 1 or len(requirement) != 1:
            raise ValidationError(
                _("The existing-authorization requirement is missing.")
            )
        if requirement.state != "pending":
            raise UserError(
                _("Existing-authorization evidence has already been recorded.")
            )
        attachment = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": self.filename,
                    "type": "binary",
                    "datas": self.evidence_file,
                    "mimetype": "application/octet-stream",
                    "res_model": process._name,
                    "res_id": process.id,
                    "public": False,
                }
            )
        )
        evidence = self.env["l10n_cl.dte.certification.evidence"]._create_from_source(
            {
                "stage_id": stage.id,
                "requirement_id": requirement.id,
                "name": self.filename,
                "evidence_type": "existing_issuer_authorization",
                "origin": "sii_portal",
                "attachment_id": attachment.id,
                "external_reference": reference,
                "source_url": source_url,
                "notes": self.notes or False,
            }
        )
        evidence.action_mark_source_verified()
        requirement._record_external_result(evidence, accepted=True)
        process._record_event(
            "existing_authorization_recorded",
            stage=stage,
            details={
                "evidence_date": fields.Date.to_string(self.evidence_date),
                "evidence_id": evidence.id,
                "evidence_sha256": evidence.sha256,
                "external_reference": reference,
                "source_url": source_url,
            },
        )
        return {"type": "ir.actions.act_window_close"}
