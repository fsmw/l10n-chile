# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo import _, fields, models
from odoo.exceptions import AccessError, ValidationError

_MAX_EVIDENCE_BYTES = 20 * 1024 * 1024


class DteCertificationEvidenceWizard(models.TransientModel):
    _name = "l10n_cl.dte.certification.evidence.wizard"
    _description = "Attach SII DTE Certification Evidence"

    stage_id = fields.Many2one(
        "l10n_cl.dte.certification.process.stage",
        required=True,
        ondelete="cascade",
    )
    requirement_id = fields.Many2one(
        "l10n_cl.dte.certification.process.requirement", ondelete="cascade"
    )
    name = fields.Char(required=True)
    evidence_type = fields.Selection(
        [
            ("original_assigned_set", "Original Assigned Set"),
            ("postulation_acceptance", "SII Postulation Acceptance"),
            ("existing_issuer_authorization", "Existing SII Issuer Authorization"),
            ("normalized_set", "Normalized Set"),
            ("signed_dte", "Signed DTE"),
            ("signed_envelope", "Signed Envelope"),
            ("sii_upload_receipt", "SII Upload Receipt"),
            ("sii_processing_result", "SII Processing Result"),
            ("sii_query_result", "SII Query Result"),
            ("incoming_dte", "Incoming DTE"),
            ("counterparty_acknowledgement", "Counterparty Acknowledgement"),
            ("printed_sample_pdf", "Printed Sample PDF"),
            ("compliance_declaration", "Compliance Declaration"),
            ("issuer_registration_resolution", "Issuer Registration Resolution"),
            ("operator_note", "Operator Note"),
            ("other", "Other"),
        ],
        required=True,
    )
    origin = fields.Selection(
        [
            ("local_generated", "Generated Locally"),
            ("sii_portal", "SII Portal"),
            ("sii_webservice", "SII Web Service"),
            ("sii_email", "SII Email"),
            ("operator_imported", "Imported by Operator"),
        ],
        required=True,
    )
    upload_file = fields.Binary(required=True)
    filename = fields.Char(required=True)
    external_reference = fields.Char()
    source_url = fields.Char()
    notes = fields.Text()

    def action_create_evidence(self):
        """Persist an immutable, hashed evidence record."""
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(
                _("Only a DTE manager can attach certification evidence.")
            )
        stage = self.stage_id
        stage._check_stage_access()
        if self.requirement_id and self.requirement_id.stage_id != stage:
            raise ValidationError(
                _("The selected requirement belongs to another stage.")
            )
        try:
            content = base64.b64decode(self.upload_file, validate=True)
        except (TypeError, ValueError) as error:
            raise ValidationError(
                _("The uploaded evidence is not valid base64.")
            ) from error
        if not content or len(content) > _MAX_EVIDENCE_BYTES:
            raise ValidationError(
                _("Evidence files must be between 1 byte and 20 MiB.")
            )
        attachment = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": self.filename,
                    "type": "binary",
                    "datas": self.upload_file,
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
                "requirement_id": self.requirement_id.id or False,
                "name": self.name.strip(),
                "evidence_type": self.evidence_type,
                "origin": self.origin,
                "attachment_id": attachment.id,
                "external_reference": (self.external_reference or "").strip() or False,
                "source_url": (self.source_url or "").strip() or False,
                "notes": self.notes or False,
            }
        )
        if self.requirement_id:
            requirement = self.requirement_id
            requirement._check_requirement_access()
            requirement.sudo()._write_internal({"evidence_ids": [(4, evidence.id)]})
        stage.process_id._record_event(
            "evidence_attached",
            stage=stage,
            details={"evidence_id": evidence.id, "sha256": evidence.sha256},
        )
        return {"type": "ir.actions.act_window_close"}
