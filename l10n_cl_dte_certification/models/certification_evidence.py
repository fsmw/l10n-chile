# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_PRIVATE_KEY_MARKERS = (
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN ENCRYPTED PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
)
_SECRET_FIELD_RE = re.compile(
    rb"(?i)(password|passwd|client_secret|access_token|refresh_token|authorization)"
    rb"\s*[:=]\s*[^\s<>&]{8,}"
)
_FORBIDDEN_EXTENSIONS = {".p12", ".pfx", ".key", ".jks"}


class DteCertificationEvidence(models.Model):
    _name = "l10n_cl.dte.certification.evidence"
    _description = "SII DTE Certification Evidence"
    _order = "captured_at desc, id desc"
    _check_company_auto = True

    process_id = fields.Many2one(
        "l10n_cl.dte.certification.process",
        required=True,
        index=True,
        check_company=True,
        ondelete="restrict",
    )
    stage_id = fields.Many2one(
        "l10n_cl.dte.certification.process.stage",
        required=True,
        index=True,
        check_company=True,
        ondelete="restrict",
    )
    requirement_id = fields.Many2one(
        "l10n_cl.dte.certification.process.requirement",
        check_company=True,
        ondelete="restrict",
    )
    company_id = fields.Many2one(
        related="process_id.company_id", store=True, index=True
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
        index=True,
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
        index=True,
    )
    attachment_id = fields.Many2one(
        "ir.attachment", required=True, ondelete="restrict", copy=False
    )
    sha256 = fields.Char(required=True, readonly=True, index=True, copy=False)
    mimetype = fields.Char(readonly=True, copy=False)
    captured_at = fields.Datetime(required=True, readonly=True, index=True)
    captured_by = fields.Many2one("res.users", required=True, readonly=True)
    external_reference = fields.Char(index=True, copy=False)
    source_url = fields.Char(copy=False)
    related_model = fields.Char(copy=False, index=True)
    related_res_id = fields.Integer(copy=False, index=True)
    verification_state = fields.Selection(
        [
            ("unverified", "Unverified"),
            ("hash_verified", "Hash Verified"),
            ("source_verified", "Source Verified"),
            ("rejected", "Rejected"),
        ],
        required=True,
        default="unverified",
        readonly=True,
        index=True,
    )
    verified_at = fields.Datetime(readonly=True, copy=False)
    verified_by = fields.Many2one("res.users", readonly=True, copy=False)
    notes = fields.Text(copy=False)

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super
        """Create evidence after digesting and screening the source attachment."""
        raise AccessError(
            _("Certification evidence must be created through its controlled wizard.")
        )

    @api.model
    def _create_from_source(self, vals):
        """Persist hashed evidence after validating the originating workflow."""
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(
                _("Only a DTE manager can attach certification evidence.")
            )
        stage = (
            self.env["l10n_cl.dte.certification.process.stage"]
            .browse(vals.get("stage_id"))
            .exists()
        )
        attachment = (
            self.env["ir.attachment"].browse(vals.get("attachment_id")).exists()
        )
        if len(stage) != 1 or len(attachment) != 1:
            raise ValidationError(_("A stage and source attachment are required."))
        stage._check_stage_access()
        if (
            attachment.res_model != stage.process_id._name
            or attachment.res_id != stage.process_id.id
        ):
            raise ValidationError(
                _("The source attachment must belong to this process.")
            )
        requirement = (
            self.env["l10n_cl.dte.certification.process.requirement"]
            .browse(vals.get("requirement_id"))
            .exists()
        )
        if vals.get("requirement_id") and (
            len(requirement) != 1 or requirement.stage_id != stage
        ):
            raise ValidationError(
                _("The requirement must belong to the selected stage.")
            )
        content = self._attachment_bytes(attachment)
        self._check_no_secret_material(content, attachment.name)
        values = vals.copy()
        values.update(
            {
                "process_id": stage.process_id.id,
                "company_id": stage.company_id.id,
                "sha256": hashlib.sha256(content).hexdigest(),
                "mimetype": attachment.mimetype or "application/octet-stream",
                "captured_at": fields.Datetime.now(),
                "captured_by": self.env.uid,
                "verification_state": "unverified",
            }
        )
        return super(DteCertificationEvidence, self.sudo()).create(values)

    def write(self, vals):
        """Prevent replacing a file or rewriting verified evidence."""
        self._check_evidence_access()
        if set(vals) - {"notes"}:
            raise UserError(
                _("Evidence identity, source and verification are immutable.")
            )
        return super().write(vals)

    def _write_internal(self, vals):
        """Update verification metadata without replacing the source artifact."""
        return super().write(vals)

    def unlink(self):  # pylint: disable=method-required-super
        """Retain evidence permanently as part of the process audit."""
        raise UserError(_("Certification evidence is retained as audit material."))

    def action_verify_integrity(self):
        """Recompute and compare the stored SHA-256 digest."""
        for evidence in self:
            evidence._check_evidence_access()
            content = evidence._attachment_bytes(evidence.attachment_id)
            actual = hashlib.sha256(content).hexdigest()
            if actual != evidence.sha256:
                evidence._write_internal({"verification_state": "rejected"})
                raise ValidationError(
                    _("The evidence file no longer matches its SHA-256.")
                )
            if evidence.verification_state == "unverified":
                evidence._write_internal({"verification_state": "hash_verified"})
        return True

    def action_mark_source_verified(self):
        """Record the reviewer and authoritative source for this evidence."""
        for evidence in self:
            evidence._check_evidence_access()
            evidence.action_verify_integrity()
            if evidence.origin == "local_generated":
                raise UserError(
                    _("Locally generated material cannot be marked as SII evidence.")
                )
            if not (evidence.external_reference or evidence.source_url):
                raise ValidationError(
                    _("A portal reference or authoritative source URL is required.")
                )
            evidence._write_internal(
                {
                    "verification_state": "source_verified",
                    "verified_at": fields.Datetime.now(),
                    "verified_by": self.env.uid,
                }
            )
        return True

    def _check_no_secret_material(self, content, filename=None):
        """Reject credentials, private keys, certificates and auth tokens."""
        extension = (
            "." + filename.rsplit(".", 1)[-1].lower()
            if filename and "." in filename
            else ""
        )
        if extension in _FORBIDDEN_EXTENSIONS or any(
            marker in content for marker in _PRIVATE_KEY_MARKERS
        ):
            raise ValidationError(
                _("Private-key files and certificate containers cannot be evidence.")
            )
        if _SECRET_FIELD_RE.search(content[:2_000_000]):
            raise ValidationError(
                _("The evidence appears to contain authentication secrets.")
            )

    def _check_company_links(self):
        """Ensure process, stage, requirement and attachment belong to this company."""
        for evidence in self:
            if evidence.stage_id.process_id != evidence.process_id:
                raise ValidationError(
                    _("The evidence stage must belong to its process.")
                )
            if (
                evidence.requirement_id
                and evidence.requirement_id.stage_id != evidence.stage_id
            ):
                raise ValidationError(
                    _("The evidence requirement must belong to its stage.")
                )
            if (
                evidence.attachment_id.res_model != evidence.process_id._name
                or evidence.attachment_id.res_id != evidence.process_id.id
            ):
                raise ValidationError(
                    _("The evidence attachment must belong to its process.")
                )

    def _compute_sha256(self):
        """Calculate SHA-256 from the actual attachment bytes."""
        self.ensure_one()
        return hashlib.sha256(self._attachment_bytes(self.attachment_id)).hexdigest()

    def _check_evidence_access(self):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(
                _("Only a DTE manager can access certification evidence.")
            )
        for evidence in self:
            evidence.process_id._check_process_access()

    @api.model
    def _attachment_bytes(self, attachment):
        if not attachment or not attachment.datas:
            raise ValidationError(_("The evidence attachment has no content."))
        try:
            return base64.b64decode(attachment.datas, validate=True)
        except (TypeError, ValueError) as error:
            raise ValidationError(
                _("The evidence attachment encoding is invalid.")
            ) from error
