# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_MAX_EVIDENCE_BYTES = 20 * 1024 * 1024
_SII_POSTULATION_STATUS_URL = "https://maullin.sii.cl/cvc_cgi/dte/pe_avance5"


class DteCertificationPostulationWizard(models.TransientModel):
    _name = "l10n_cl.dte.certification.postulation.wizard"
    _description = "Record SII Postulation Acceptance"

    process_id = fields.Many2one(
        "l10n_cl.dte.certification.process",
        string="Certification Process",
        required=True,
        ondelete="cascade",
    )
    is_supplemental = fields.Boolean(compute="_compute_is_supplemental")
    application_reference = fields.Char(
        string="Initial SII Postulation Reference (optional)",
        help=(
            "Enter a reference only if the SII explicitly provided one for the "
            "original postulation. The postulation status page may show only "
            "the certification stage; in that case leave this field empty and "
            "attach the SII evidence. This is not the SET identifier or the "
            "shipment ID/TrackID from the SETMAIL file."
        ),
    )
    source_url = fields.Char(
        string="SII Postulation Status Page",
        help=(
            "Official destination of the SII menu option “Ver estado de "
            "postulación”. This is stored as evidence provenance; it does not "
            "automatically query the SII."
        ),
        default=_SII_POSTULATION_STATUS_URL,
        readonly=True,
    )
    accepted_at = fields.Date(
        string="SII Acceptance Date", required=True, default=fields.Date.context_today
    )
    evidence_file = fields.Binary(string="Acceptance Evidence", required=True)
    filename = fields.Char(string="File Name", required=True)
    notes = fields.Text()

    @api.depends("process_id")
    def _compute_is_supplemental(self):
        for wizard in self:
            stages = wizard.process_id.stage_ids.filtered(
                lambda item: item.code == "assigned_set"
            )
            requirements = stages.mapped("requirement_ids").filtered(
                lambda item: item.code == "accepted_postulation"
            )
            wizard.is_supplemental = bool(
                len(requirements) == 1 and requirements.state == "accepted"
            )

    def action_apply(self):
        """Capture the operator-reviewed SII acceptance before process start."""
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(
                _("Only a DTE manager can record an accepted SII postulation.")
            )
        process = self.process_id
        process._check_process_access()
        if process.process_mode != "new_postulation":
            raise UserError(
                _(
                    "Postulation evidence is not valid for an already-authorized "
                    "taxpayer."
                )
            )
        assigned_stage = process.stage_ids.filtered(
            lambda item: item.code == "assigned_set"
        )
        requirement = process._ensure_postulation_requirement()
        is_legacy_assigned_stage = (
            process.started_at
            and process.current_stage_code == "assigned_set"
            and assigned_stage.state == "pending"
            and requirement.state == "pending"
        )
        if process.started_at and not is_legacy_assigned_stage:
            raise UserError(_("Postulation acceptance can no longer be changed."))
        reference = (self.application_reference or "").strip()
        stage = assigned_stage
        if requirement.state not in {"pending", "accepted"}:
            raise UserError(
                _(
                    "Postulation evidence can only be added while its requirement "
                    "is pending or accepted."
                )
            )
        is_supplemental = requirement.state == "accepted"
        if is_supplemental and len((self.notes or "").strip()) < 8:
            raise ValidationError(
                _("Explain why additional postulation evidence is being attached.")
            )
        try:
            content = base64.b64decode(self.evidence_file, validate=True)
        except (TypeError, ValueError) as error:
            raise ValidationError(
                _("The postulation evidence is not valid base64.")
            ) from error
        if not content or len(content) > _MAX_EVIDENCE_BYTES:
            raise ValidationError(
                _("Attach non-empty SII postulation evidence up to 20 MiB.")
            )
        if (self.filename or "").upper().startswith("SETMAIL"):
            raise ValidationError(
                _(
                    "A SETMAIL file documents the assigned test-set result, not "
                    "initial postulation acceptance. Attach the SII postulation "
                    "status screenshot or its official document here."
                )
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
                "evidence_type": "postulation_acceptance",
                "origin": "sii_portal",
                "attachment_id": attachment.id,
                "external_reference": reference or False,
                "source_url": self.source_url,
                "notes": self.notes or False,
            }
        )
        superseded_evidence = requirement.evidence_ids.filtered(
            lambda item: (
                item.evidence_type == "postulation_acceptance"
                and item.attachment_id.name.upper().startswith("SETMAIL")
                and item.verification_state == "source_verified"
            )
        )
        superseded_note = _(
            "Superseded as postulation evidence: this SETMAIL file records an "
            "assigned test-set result. The original file is retained for audit."
        )
        for prior_evidence in superseded_evidence:
            prior_evidence._write_internal(
                {
                    "verification_state": "rejected",
                    "notes": "\n".join(
                        filter(None, [prior_evidence.notes, superseded_note])
                    ),
                }
            )
        evidence.action_mark_source_verified()
        requirement._record_external_result(evidence, accepted=True)
        process._write_internal({"sii_application_reference": reference})
        event_code = (
            "postulation_acceptance_evidence_appended"
            if is_supplemental
            else "legacy_postulation_acceptance_recorded"
            if is_legacy_assigned_stage
            else "postulation_acceptance_recorded"
        )
        process._record_event(
            event_code,
            stage=stage,
            details={
                "application_reference": reference,
                "accepted_at": fields.Date.to_string(self.accepted_at),
                "evidence_id": evidence.id,
                "evidence_sha256": evidence.sha256,
                "supplemental": is_supplemental,
                "previous_evidence_ids": (
                    (requirement.evidence_ids - evidence).ids
                    if is_supplemental
                    else []
                ),
                "superseded_evidence_ids": superseded_evidence.ids,
                "supplemental_reason": self.notes if is_supplemental else False,
            },
        )
        return {"type": "ir.actions.act_window_close"}
