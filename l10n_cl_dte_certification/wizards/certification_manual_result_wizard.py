# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class DteCertificationManualResultWizard(models.TransientModel):
    _name = "l10n_cl.dte.certification.manual.result.wizard"
    _description = "Register Manual SII Certification Result"

    stage_id = fields.Many2one(
        "l10n_cl.dte.certification.process.stage",
        required=True,
        ondelete="cascade",
    )
    requirement_id = fields.Many2one(
        "l10n_cl.dte.certification.process.requirement",
        required=True,
        ondelete="cascade",
        domain=(
            "[('stage_id', '=', stage_id), ('external_result', '=', True), "
            "('state', '=', 'pending')]"
        ),
    )
    result = fields.Selection(
        [("accepted", "Accepted"), ("rejected", "Rejected")], required=True
    )
    decision_date = fields.Date(required=True)
    source = fields.Selection(
        [("sii_portal", "SII Portal"), ("sii_email", "SII Email")],
        required=True,
    )
    external_reference = fields.Char(required=True)
    resolution_number = fields.Char()
    authorized_document_types = fields.Char(
        help=(
            "Comma-separated DTE codes shown in the SII issuer registration resolution."
        )
    )
    evidence_file = fields.Binary(required=True)
    filename = fields.Char(required=True)
    notes = fields.Text()

    def action_apply(self):
        """Register a manual portal outcome with its source evidence."""
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(_("Only a DTE manager can register SII results."))
        stage = self.stage_id
        stage._check_stage_access()
        existing_assigned_set_result = (
            stage.state == "ready" and stage._has_accepted_assigned_set_envelope()
        )
        if stage.state not in {"submitted", "waiting_sii"} and not (
            existing_assigned_set_result
        ):
            raise UserError(_("Record an SII result only for a submitted stage."))
        external_reference = (self.external_reference or "").strip()
        if not external_reference:
            raise ValidationError(_("An SII portal reference is required."))
        try:
            content = base64.b64decode(self.evidence_file, validate=True)
        except (TypeError, ValueError) as error:
            raise ValidationError(
                _("The result attachment is not valid base64.")
            ) from error
        if not content:
            raise ValidationError(_("Attach the official SII result."))
        evidence_type = {
            "compliance_declaration": "compliance_declaration",
            "issuer_registration": "issuer_registration_resolution",
        }.get(stage.code, "sii_processing_result")
        result_requirement = self.requirement_id
        if (
            result_requirement.stage_id != stage
            or not result_requirement.external_result
            or result_requirement.code == "accepted_postulation"
            or result_requirement.state != "pending"
        ):
            raise ValidationError(
                _("Choose a pending SII result requirement belonging to this stage.")
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
                    "res_model": stage.process_id._name,
                    "res_id": stage.process_id.id,
                    "public": False,
                }
            )
        )
        evidence = self.env["l10n_cl.dte.certification.evidence"]._create_from_source(
            {
                "stage_id": stage.id,
                "requirement_id": result_requirement.id,
                "name": self.filename,
                "evidence_type": evidence_type,
                "origin": self.source,
                "attachment_id": attachment.id,
                "external_reference": external_reference,
                "notes": self.notes or False,
            }
        )
        evidence.action_mark_source_verified()
        state = "accepted" if self.result == "accepted" else "rejected"
        result_requirement._record_external_result(
            evidence, accepted=state == "accepted"
        )
        decided_at = fields.Datetime.to_datetime(self.decision_date)
        stage._write_internal(
            {
                "state": state,
                "decided_at": decided_at,
                "external_reference": external_reference,
                "sii_status": state,
                "sii_message": self.notes or False,
            }
        )
        process = stage.process_id
        if stage.code == "issuer_registration" and state == "accepted":
            document_types = [
                item.strip()
                for item in (self.authorized_document_types or "").split(",")
                if item.strip()
            ]
            if not self.resolution_number or not document_types:
                raise ValidationError(
                    _(
                        "Enter the resolution number and authorized DTE types "
                        "from the SII."
                    )
                )
            if any(not item.isdigit() for item in document_types):
                raise ValidationError(_("Authorized DTE types must be numeric codes."))
            process._write_internal(
                {
                    "resolution_number": self.resolution_number.strip(),
                    "resolution_date": self.decision_date,
                    "authorized_document_types": sorted(set(document_types)),
                    "completed_at": decided_at,
                }
            )
        process._record_event(
            "stage_manual_result_" + state,
            stage=stage,
            from_state="ready" if existing_assigned_set_result else "submitted",
            to_state=state,
            details={
                "external_reference": external_reference,
                "evidence_id": evidence.id,
                "evidence_sha256": evidence.sha256,
                "decision_date": self.decision_date.isoformat(),
            },
        )
        return {"type": "ir.actions.act_window_close"}

    @api.onchange("stage_id")
    def _onchange_stage_id(self):
        for wizard in self:
            requirements = wizard.stage_id.requirement_ids.filtered(
                lambda item: item.external_result and item.state == "pending"
            )
            wizard.requirement_id = requirements if len(requirements) == 1 else False
