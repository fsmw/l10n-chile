# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class DteCertificationProcessRequirement(models.Model):
    _name = "l10n_cl.dte.certification.process.requirement"
    _description = "SII DTE Certification Stage Requirement"
    _order = "stage_id, sequence, id"
    _check_company_auto = True
    _sql_constraints = [
        (
            "stage_requirement_code_uniq",
            "unique(stage_id, code)",
            "A stage requirement code must be unique within its stage.",
        ),
    ]

    stage_id = fields.Many2one(
        "l10n_cl.dte.certification.process.stage",
        required=True,
        index=True,
        check_company=True,
        ondelete="restrict",
    )
    template_id = fields.Many2one(
        "l10n_cl.dte.certification.requirement.template",
        required=True,
        index=True,
        ondelete="restrict",
        copy=False,
    )
    company_id = fields.Many2one(related="stage_id.company_id", store=True, index=True)
    code = fields.Char(required=True, index=True, copy=False)
    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(required=True, default=10)
    mandatory = fields.Boolean(default=True, required=True)
    external_result = fields.Boolean(readonly=True)
    applicability = fields.Selection(
        [
            ("required", "Required"),
            ("conditional", "Conditional"),
            ("not_applicable", "Not Applicable"),
        ],
        required=True,
        default="required",
    )
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("ready", "Ready"),
            ("accepted", "Accepted"),
            ("rejected", "Rejected"),
            ("blocked", "Blocked"),
            ("not_applicable", "Not Applicable"),
        ],
        required=True,
        default="pending",
        readonly=True,
        index=True,
    )
    normative_title = fields.Char(required=True)
    normative_url = fields.Char(required=True)
    normative_version = fields.Char(required=True)
    normative_sha256 = fields.Char(readonly=True, copy=False)
    applicability_reason = fields.Text(copy=False)
    control_owner_id = fields.Many2one(
        "res.users",
        string="Control Owner",
        check_company=True,
        ondelete="restrict",
        copy=False,
    )
    procedure_version = fields.Char(copy=False)
    last_review_date = fields.Date(copy=False)
    control_result = fields.Selection(
        [("passed", "Passed"), ("failed", "Failed")], copy=False
    )
    requires_operational_control_review = fields.Boolean(
        compute="_compute_requires_operational_control_review"
    )
    satisfied_at = fields.Datetime(readonly=True, copy=False)
    satisfied_by = fields.Many2one("res.users", readonly=True, copy=False)
    evidence_ids = fields.Many2many(
        "l10n_cl.dte.certification.evidence",
        relation="l10n_cl_cert_req_evidence_rel",
        column1="requirement_id",
        column2="evidence_id",
        copy=False,
    )
    diagnostic = fields.Text(readonly=True, copy=False)

    @api.constrains("template_id", "stage_id")
    def _check_template_stage(self):
        """Keep each copied checklist item on its template's official stage."""
        for requirement in self:
            if requirement.template_id.stage_code != requirement.stage_id.code:
                raise ValidationError(
                    _("A requirement template must match its owning process stage.")
                )
            if requirement.code != requirement.template_id.code:
                raise ValidationError(
                    _("A requirement code is copied from its template.")
                )

    @api.model_create_multi
    def _create_internal(self, vals_list):
        """Create copied checklist requirements from immutable templates."""
        normalized = []
        for incoming in vals_list:
            values = incoming.copy()
            stage = (
                self.env["l10n_cl.dte.certification.process.stage"]
                .browse(values.get("stage_id"))
                .exists()
            )
            template = (
                self.env["l10n_cl.dte.certification.requirement.template"]
                .browse(values.get("template_id"))
                .exists()
            )
            if len(stage) != 1 or len(template) != 1:
                raise ValidationError(
                    _("A stage and requirement template are required.")
                )
            if template.stage_code != stage.code:
                raise ValidationError(
                    _("The requirement template belongs to a different stage.")
                )
            values.update(
                {
                    "code": template.code,
                    "mandatory": template.mandatory,
                    "external_result": template.external_result,
                    "applicability": template.applicability,
                    "state": "pending",
                }
            )
            normalized.append(values)
        return super(DteCertificationProcessRequirement, self.sudo()).create(normalized)

    def _check_requirement_access(self):
        """Validate access through the owning certification process."""
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(
                _("Only a DTE manager can update certification requirements.")
            )
        for requirement in self:
            requirement.stage_id._check_stage_access()

    def action_mark_ready(self):
        """Mark the requirement ready only when its evidence is complete."""
        for requirement in self:
            requirement._check_requirement_access()
            if requirement.applicability == "not_applicable":
                raise UserError(
                    _("A not-applicable requirement cannot be marked ready.")
                )
            if requirement.external_result:
                raise UserError(
                    _(
                        "External-result requirements must be satisfied from "
                        "verified SII evidence."
                    )
                )
            evidence = requirement.evidence_ids
            if not evidence:
                raise ValidationError(
                    _("Attach evidence before marking a requirement ready.")
                )
            for item in evidence:
                item.action_verify_integrity()
            if requirement.requires_operational_control_review:
                if (
                    not requirement.control_owner_id
                    or not requirement.procedure_version
                    or not requirement.last_review_date
                    or requirement.control_result != "passed"
                ):
                    raise ValidationError(
                        _(
                            "Complete the control owner, procedure version, review "
                            "date and passing result before marking this control ready."
                        )
                    )
            requirement._write_internal({"state": "ready", "diagnostic": False})
            requirement.stage_id.process_id._record_event(
                "requirement_marked_ready",
                stage=requirement.stage_id,
                details={"requirement_code": requirement.code},
            )
        return True

    def _record_external_result(self, evidence, accepted):
        """Link a verified SII result to its designated stage requirement."""
        for requirement in self:
            requirement._check_requirement_access()
            if not requirement.external_result:
                raise ValidationError(
                    _("Only an external-result requirement can record an SII decision.")
                )
            if evidence.stage_id != requirement.stage_id:
                raise ValidationError(
                    _("The SII result evidence must belong to the same stage.")
                )
            if evidence.verification_state != "source_verified":
                raise ValidationError(
                    _("Verify the SII source before applying its decision.")
                )
            if evidence.requirement_id != requirement:
                raise ValidationError(
                    _("The evidence must identify the requirement it satisfies.")
                )
            state = "accepted" if accepted else "rejected"
            requirement._write_internal(
                {
                    "state": state,
                    "evidence_ids": [(4, evidence.id)],
                    "satisfied_at": fields.Datetime.now() if accepted else False,
                    "satisfied_by": self.env.uid if accepted else False,
                    "diagnostic": False if accepted else evidence.notes,
                }
            )
            requirement.stage_id.process_id._record_event(
                "external_requirement_" + state,
                stage=requirement.stage_id,
                details={
                    "requirement_code": requirement.code,
                    "evidence_id": evidence.id,
                    "evidence_sha256": evidence.sha256,
                    "external_reference": evidence.external_reference,
                },
            )
        return True

    def action_accept(self):
        """Accept the requirement from an SII result or reviewed evidence."""
        for requirement in self:
            requirement._check_requirement_access()
            if requirement.state != "ready" or not requirement.evidence_ids:
                raise UserError(
                    _("Only a ready requirement with evidence can be accepted.")
                )
            requirement._write_internal(
                {
                    "state": "accepted",
                    "satisfied_at": fields.Datetime.now(),
                    "satisfied_by": self.env.uid,
                }
            )
            requirement.stage_id.process_id._record_event(
                "requirement_accepted",
                stage=requirement.stage_id,
                details={"requirement_code": requirement.code},
            )
        return True

    def action_mark_not_applicable(self):
        """Mark a conditional requirement not applicable with justification."""
        self.ensure_one()
        self._check_requirement_access()
        reason = (self.applicability_reason or "").strip()
        if self.mandatory or self.template_id.applicability != "conditional":
            raise UserError(
                _("Only an optional conditional requirement can be excluded.")
            )
        if len(reason) < 12:
            raise ValidationError(_("Provide a specific reason for this exclusion."))
        self._write_internal(
            {"state": "not_applicable", "applicability": "not_applicable"}
        )
        self.stage_id.process_id._record_event(
            "requirement_excluded",
            stage=self.stage_id,
            details={"requirement_code": self.code, "reason": reason[:1000]},
        )
        return True

    def action_reject(self):
        """Record a failed requirement with an actionable diagnostic."""
        self.ensure_one()
        self._check_requirement_access()
        if not self.diagnostic or len(self.diagnostic.strip()) < 8:
            raise ValidationError(_("Enter an actionable rejection reason."))
        self._write_internal({"state": "rejected"})
        self.stage_id.process_id._record_event(
            "requirement_rejected",
            stage=self.stage_id,
            details={"requirement_code": self.code, "reason": self.diagnostic[:1000]},
        )
        return True

    def write(self, vals):
        """Prevent historical requirement definitions from being rewritten."""
        editable_fields = {
            "applicability_reason",
            "control_owner_id",
            "procedure_version",
            "last_review_date",
            "control_result",
        }
        if set(vals) and set(vals).issubset(editable_fields):
            self._check_requirement_access()
            if any(requirement.state != "pending" for requirement in self):
                raise UserError(
                    _(
                        "Requirement evidence and control details are frozen after "
                        "review."
                    )
                )
            if vals.get("control_owner_id"):
                owner = self.env["res.users"].browse(vals["control_owner_id"])
                if owner.company_id not in self.mapped("company_id") and not (
                    owner.company_ids & self.mapped("company_id")
                ):
                    raise ValidationError(
                        _("The control owner must have access to the dossier company.")
                    )
            return super().write(vals)
        raise UserError(_("Use requirement actions to change checklist status."))

    @api.depends("stage_id.code", "code")
    def _compute_requires_operational_control_review(self):
        """Identify the seven SII controls, excluding the portal declaration."""
        control_codes = {
            "compliance_caf_access_control",
            "compliance_unique_foliation",
            "compliance_backups",
            "compliance_sii_outbound",
            "compliance_document_exchange",
            "compliance_sii_reconciliation",
            "compliance_contingency_management",
        }
        for requirement in self:
            requirement.requires_operational_control_review = (
                requirement.stage_id.code == "compliance_declaration"
                and requirement.code in control_codes
            )

    def _write_internal(self, vals):
        """Update requirement state from a validated checklist action."""
        return super().write(vals)

    # pylint: disable=method-required-super
    def unlink(self):
        """Retain checklist history."""
        raise UserError(_("Certification requirements are retained as audit evidence."))
