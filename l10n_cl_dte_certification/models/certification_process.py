# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import SQL

from ..services.access import check_certification_access

_PROCESS_STAGES = (
    ("assigned_set", 10),
    ("simulation_set", 20),
    ("information_exchange", 30),
    ("printed_samples", 40),
    ("compliance_declaration", 50),
    ("issuer_registration", 60),
)
_PROCESS_PROTECTED_FIELDS = {
    "state",
    "current_stage_code",
    "started_at",
    "completed_at",
    "environment",
    "resolution_number",
    "resolution_date",
    "authorized_document_types",
    "readiness_diagnostics",
}


class DteCertificationProcess(models.Model):
    _name = "l10n_cl.dte.certification.process"
    _description = "SII DTE Certification Process"
    _order = "create_date desc, id desc"
    _check_company_auto = True
    _sql_constraints = [
        (
            "certification_process_name_uniq",
            "unique(name)",
            "Certification process names must be unique.",
        ),
    ]

    name = fields.Char(required=True, readonly=True, copy=False, index=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        index=True,
        default=lambda self: self.env.company,
        ondelete="restrict",
    )
    sii_application_reference = fields.Char(
        string="Initial SII Postulation Reference (optional)",
        help=(
            "Enter a reference only if the SII explicitly provided one for the "
            "original postulation. The postulation status page may show only "
            "the certification stage; in that case leave this field empty and "
            "attach the SII evidence. This is not the SET identifier or the "
            "shipment ID/TrackID from the SETMAIL file."
        ),
        copy=False,
        index=True,
    )
    process_mode = fields.Selection(
        [
            ("new_postulation", "New SII Postulation"),
            ("assigned_test_set", "SII Assigned Test Set"),
            ("authorized_taxpayer", "Already Authorized Taxpayer"),
        ],
        required=True,
        default="new_postulation",
        help=(
            "Choose SII Assigned Test Set when the SII has already granted "
            "Certification access and assigned a test set. The official SETMAIL "
            "decision is still required to close that first stage."
        ),
        copy=False,
        index=True,
    )
    environment = fields.Selection(
        [("certification", "Certification")],
        required=True,
        default="certification",
        readonly=True,
        copy=False,
    )
    certification_scope = fields.Selection(
        [
            ("dte", "Invoices and Other DTE"),
            ("boleta", "Electronic Sales Tickets"),
        ],
        required=True,
        default="dte",
        index=True,
        copy=False,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("in_progress", "In Progress"),
            ("waiting_sii", "Waiting for SII"),
            ("blocked", "Blocked"),
            ("completed", "Completed"),
        ],
        required=True,
        compute="_compute_state",
        store=True,
        default="draft",
        readonly=True,
        index=True,
        copy=False,
    )
    current_stage_code = fields.Selection(
        [
            ("assigned_set", "Assigned Test Set"),
            ("simulation_set", "Simulation Set"),
            ("information_exchange", "Information Exchange"),
            ("printed_samples", "Printed Samples"),
            ("compliance_declaration", "Compliance Declaration"),
            ("issuer_registration", "Electronic Issuer Registration"),
        ],
        compute="_compute_current_stage_code",
        store=True,
        readonly=True,
    )
    started_at = fields.Datetime(readonly=True, copy=False)
    completed_at = fields.Datetime(readonly=True, copy=False)
    certification_set_ids = fields.Many2many(
        "l10n_cl.dte.certification.set",
        relation="l10n_cl_cert_process_set_rel",
        column1="process_id",
        column2="set_id",
        copy=False,
    )
    stage_ids = fields.One2many(
        "l10n_cl.dte.certification.process.stage", "process_id", copy=False
    )
    evidence_ids = fields.One2many(
        "l10n_cl.dte.certification.evidence", "process_id", copy=False
    )
    event_ids = fields.One2many(
        "l10n_cl.dte.certification.process.event", "process_id", copy=False
    )
    resolution_number = fields.Char(readonly=True, copy=False)
    resolution_date = fields.Date(readonly=True, copy=False)
    authorized_document_types = fields.Json(readonly=True, copy=False)
    readiness_diagnostics = fields.Json(readonly=True, copy=False)
    active = fields.Boolean(default=True, index=True)

    def init(self):
        """Keep one active dossier for each company/SII application."""
        self.env.cr.execute(
            SQL(
                "CREATE UNIQUE INDEX IF NOT EXISTS %s ON %s "
                "(company_id, sii_application_reference) "
                "WHERE active AND sii_application_reference IS NOT NULL",
                SQL.identifier("l10n_cl_cert_process_application_uniq"),
                SQL.identifier(self._table),
            )
        )

    @api.constrains("company_id", "certification_scope", "certification_set_ids")
    def _check_process_links(self):
        """Keep every linked set inside the process company."""
        for process in self:
            for certification_set in process.certification_set_ids:
                if (
                    certification_set.company_id != process.company_id
                    or certification_set.sii_environment != "certification"
                ):
                    raise ValidationError(
                        _(
                            "A process can only include Certification sets "
                            "for its company."
                        )
                    )
                codes = set(certification_set.case_ids.mapped("document_code"))
                process._validate_scope_codes(process.certification_scope, codes)

    @api.constrains("process_mode", "sii_application_reference")
    def _check_application_reference_mode(self):
        for process in self:
            if (
                process.process_mode == "authorized_taxpayer"
                and process.sii_application_reference
            ):
                raise ValidationError(
                    _(
                        "An already-authorized taxpayer process cannot have a "
                        "new-postulation reference."
                    )
                )

    @api.model
    def _validate_scope_codes(self, scope, document_codes):
        """Keep invoice and boleta certification approvals independent."""
        document_codes = set(document_codes)
        if scope == "boleta" and document_codes - {"39", "41"}:
            raise ValidationError(
                _(
                    "A boleta certification process can only include document "
                    "types 39 and 41."
                )
            )
        if scope == "dte" and document_codes.intersection({"39", "41"}):
            raise ValidationError(
                _("Boleta types 39 and 41 require their own certification process.")
            )

    @api.depends("stage_ids.code", "stage_ids.state", "stage_ids.sequence")
    def _compute_current_stage_code(self):
        """Select the first official certification stage not accepted."""
        for process in self:
            pending = process.stage_ids.filtered(
                lambda stage: stage.state != "accepted"
            ).sorted(lambda stage: (stage.sequence, stage.id))
            process.current_stage_code = pending[:1].code if pending else False

    @api.depends(
        "stage_ids.state",
        "started_at",
        "resolution_number",
        "resolution_date",
        "evidence_ids.evidence_type",
        "evidence_ids.verification_state",
    )
    def _compute_state(self):
        """Derive the process state without inventing SII acceptance."""
        for process in self:
            states = set(process.stage_ids.mapped("state"))
            if not process.started_at:
                state = "draft"
            elif (
                len(process.stage_ids) == len(_PROCESS_STAGES)
                and all(stage.state == "accepted" for stage in process.stage_ids)
                and process.resolution_number
                and process.resolution_date
                and process.evidence_ids.filtered(
                    lambda item: (
                        item.evidence_type == "issuer_registration_resolution"
                        and item.verification_state == "source_verified"
                    )
                )
            ):
                state = "completed"
            elif "rejected" in states or "blocked" in states:
                state = "blocked"
            elif "submitted" in states or "waiting_sii" in states:
                state = "waiting_sii"
            else:
                state = "in_progress"
            process.state = state

    @api.model_create_multi
    def create(self, vals_list):
        """Create a dossier and its six ordered stages transactionally."""
        self._check_manager()
        normalized = []
        for incoming in vals_list:
            values = incoming.copy()
            if _PROCESS_PROTECTED_FIELDS.intersection(values) or {
                "name",
                "active",
            }.intersection(values):
                raise ValidationError(
                    _("Process state and identity are assigned by the workflow.")
                )
            company = (
                self.env["res.company"]
                .browse(values.get("company_id") or self.env.company.id)
                .exists()
            )
            if len(company) != 1:
                raise ValidationError(_("Select one valid certification company."))
            if company.country_id.code != "CL" or not company.vat:
                raise ValidationError(
                    _("Certification requires a Chilean company with a tax ID.")
                )
            check_certification_access(self.env, company)
            values.update(
                {
                    "name": self.env["ir.sequence"].next_by_code(
                        "l10n_cl.dte.certification.process"
                    ),
                    "company_id": company.id,
                    "environment": "certification",
                    "active": True,
                }
            )
            normalized.append(values)
        processes = super().create(normalized)
        processes._create_stage_checklists()
        processes._configure_entry_requirements()
        return processes

    def write(self, vals):
        """Protect stage outcomes and evidence-backed registration fields."""
        self._check_process_access()
        if _PROCESS_PROTECTED_FIELDS.intersection(vals) or "active" in vals:
            raise UserError(_("Use certification workflow actions to change state."))
        if set(vals) - {
            "sii_application_reference",
            "certification_set_ids",
            "certification_scope",
            "process_mode",
        }:
            raise UserError(
                _(
                    "Only the certification mode, application reference, document "
                    "scope and linked sets may change."
                )
            )
        if any(process.started_at for process in self):
            raise UserError(_("Process metadata is frozen after certification starts."))
        if "process_mode" in vals:
            for process in self.filtered(
                lambda item: item.process_mode != vals["process_mode"]
            ):
                if any(process._entry_requirements().mapped("evidence_ids")):
                    raise UserError(
                        _(
                            "The certification mode cannot change after evidence "
                            "is recorded."
                        )
                    )
        result = super().write(vals)
        if "process_mode" in vals:
            self._configure_entry_requirements()
            for process in self:
                process._record_event(
                    "certification_mode_changed",
                    details={"process_mode": process.process_mode},
                )
        return result

    def unlink(self):  # pylint: disable=method-required-super
        """Retain certification history instead of deleting it."""
        raise UserError(_("Certification processes are retained as audit evidence."))

    def _write_internal(self, vals):
        """Update protected fields after a validated workflow transition."""
        return super().write(vals)

    def _check_process_access(self):
        """Revalidate group, company and immutable Certification environment."""
        for process in self:
            check_certification_access(self.env, process.company_id)
            if process.environment != "certification":
                raise UserError(_("A certification process must use Certification."))

    def _check_company_readiness(self):
        """Validate issuer, signer, profile, CAF and CAF trust-key readiness."""
        self.ensure_one()
        diagnostics = []
        company = self.company_id
        if company.country_id.code != "CL":
            diagnostics.append("company_not_chilean")
        if not company.vat:
            diagnostics.append("company_tax_id_missing")
        profile = self.env["l10n_cl.dte.signing.profile"].search(
            [
                ("company_id", "=", company.id),
                ("sii_environment", "=", "certification"),
                ("active", "=", True),
            ],
            limit=1,
        )
        if not profile:
            diagnostics.append("certification_signing_profile_missing")
        elif (
            not profile.certificate_id.private_key_id
            or profile.certificate_id.private_key_id.public
        ):
            diagnostics.append("certification_signing_private_key_missing")
        cafs = self.env["l10n_cl.caf.authorization"].search(
            [
                ("company_id", "=", company.id),
                ("sii_environment", "=", "certification"),
                ("state", "=", "validated"),
            ]
        )
        if not cafs:
            diagnostics.append("certification_caf_missing")
        elif any(caf.sii_signature_status != "verified" for caf in cafs):
            diagnostics.append("caf_sii_signature_not_verified")
        return diagnostics

    def _create_stage_checklists(self):
        """Copy active requirement template revisions to six process stages."""
        stage_model = self.env["l10n_cl.dte.certification.process.stage"].sudo()
        template_model = self.env[
            "l10n_cl.dte.certification.requirement.template"
        ].sudo()
        requirement_model = self.env[
            "l10n_cl.dte.certification.process.requirement"
        ].sudo()
        for process in self:
            process._check_process_access()
            for code, sequence in _PROCESS_STAGES:
                stage = stage_model._create_from_process(
                    {"process_id": process.id, "code": code, "sequence": sequence}
                )
                templates = template_model.search(
                    [("stage_code", "=", code), ("active", "=", True)],
                    order="sequence, id",
                )
                if not templates:
                    raise ValidationError(
                        _("No active requirement templates exist for stage %s.") % code
                    )
                requirement_model._create_internal(
                    [
                        {
                            "stage_id": stage.id,
                            "template_id": template.id,
                            "code": template.code,
                            "name": template.name,
                            "sequence": template.sequence,
                            "mandatory": template.mandatory,
                            "external_result": template.external_result,
                            "applicability": template.applicability,
                            "normative_title": template.normative_title,
                            "normative_url": template.normative_url,
                            "normative_version": template.normative_version,
                            "normative_sha256": template.normative_sha256,
                        }
                        for template in templates
                    ]
                )

    def _ensure_postulation_requirement(self):
        """Backfill the postulation snapshot for dossiers created before the gate."""
        requirement = self._entry_requirements().filtered(
            lambda item: item.code == "accepted_postulation"
        )
        if len(requirement) != 1:
            raise ValidationError(
                _("The accepted-postulation requirement is duplicated or missing.")
            )
        return requirement

    def _check_certification_entry(self):
        """Require evidence appropriate to this taxpayer's certification path."""
        self.ensure_one()
        if self.process_mode == "assigned_test_set":
            return self._validate_assigned_test_set_entry()
        requirements = self._entry_requirements()
        code, evidence_type = (
            ("accepted_postulation", "postulation_acceptance")
            if self.process_mode == "new_postulation"
            else ("existing_authorization", "existing_issuer_authorization")
        )
        requirement = requirements.filtered(lambda item: item.code == code)
        if (
            len(requirement) != 1
            or requirement.state != "accepted"
            or not requirement.evidence_ids.filtered(
                lambda item: (
                    item.evidence_type == evidence_type
                    and item.origin == "sii_portal"
                    and item.verification_state == "source_verified"
                    and (
                        not self.sii_application_reference
                        or item.external_reference == self.sii_application_reference
                    )
                )
            )
        ):
            raise UserError(self._entry_gate_error())
        return requirement

    def _validate_assigned_test_set_entry(self):
        """Require an SII-assigned set and accepted envelope to start its cycle."""
        self.ensure_one()
        assigned_stage = self.stage_ids.filtered(
            lambda item: item.code == "assigned_set"
        )
        if len(assigned_stage) != 1 or not assigned_stage.certification_set_id:
            raise UserError(
                _(
                    "Link the SII-assigned test set before starting this "
                    "certification."
                )
            )
        certification_set = assigned_stage.certification_set_id
        run, submission = certification_set._get_sii_advance_result()
        if (
            not run
            or run not in assigned_stage.certification_run_ids
            or not submission
            or submission not in assigned_stage.sii_envelope_submission_ids
        ):
            raise UserError(
                _(
                    "The complete assigned set must have an accepted SII envelope. "
                    "Record its official SETMAIL result in the Assigned Test Set "
                    "stage."
                )
            )
        return assigned_stage

    def _entry_gate_error(self):
        self.ensure_one()
        if self.process_mode == "authorized_taxpayer":
            return _(
                "Record source evidence of the taxpayer's existing SII "
                "authorization before starting certification."
            )
        return _(
            "Record the SII-accepted postulation and its portal evidence "
            "before starting certification."
        )

    def _entry_requirements(self):
        self.ensure_one()
        stage = self.stage_ids.filtered(lambda item: item.code == "assigned_set")
        if len(stage) != 1:
            raise ValidationError(_("The assigned-set stage is missing."))
        requirements = stage.requirement_ids.filtered(
            lambda item: item.code in {"accepted_postulation", "existing_authorization"}
        )
        created_requirement = False
        for code in ("accepted_postulation", "existing_authorization"):
            if any(item.code == code for item in requirements):
                continue
            template = (
                self.env["l10n_cl.dte.certification.requirement.template"]
                .sudo()
                .search(
                    [
                        ("stage_code", "=", "assigned_set"),
                        ("code", "=", code),
                        ("active", "=", True),
                    ],
                    order="normative_version desc, id desc",
                    limit=1,
                )
            )
            if not template:
                raise ValidationError(
                    _("The active certification entry requirement %s is missing.")
                    % code
                )
            created = self.env[
                "l10n_cl.dte.certification.process.requirement"
            ]._create_internal(
                [
                    {
                        "stage_id": stage.id,
                        "template_id": template.id,
                        "code": template.code,
                        "name": template.name,
                        "sequence": template.sequence,
                        "mandatory": template.mandatory,
                        "external_result": template.external_result,
                        "applicability": template.applicability,
                        "normative_title": template.normative_title,
                        "normative_url": template.normative_url,
                        "normative_version": template.normative_version,
                        "normative_sha256": template.normative_sha256,
                    }
                ]
            )
            requirements |= created
            created_requirement = True
        if created_requirement:
            self._apply_entry_requirement_mode(requirements)
        if len(requirements) != 2:
            raise ValidationError(
                _("Both certification entry requirements must be configured.")
            )
        return requirements

    def _configure_entry_requirements(self):
        """Make the applicable authorization gate mandatory and the other N/A."""
        for process in self:
            requirements = process._entry_requirements()
            process._apply_entry_requirement_mode(requirements)

    def _apply_entry_requirement_mode(self, requirements):
        """Apply this process mode to the two authorization checklist lines."""
        for process in self:
            if len(requirements) != 2:
                raise ValidationError(
                    _("Both certification entry requirements must be configured.")
                )
            required_code = {
                "new_postulation": "accepted_postulation",
                "authorized_taxpayer": "existing_authorization",
            }.get(process.process_mode)
            for requirement in requirements:
                if required_code and requirement.code == required_code:
                    requirement._write_internal(
                        {
                            "mandatory": True,
                            "applicability": "required",
                            "state": (
                                "pending"
                                if requirement.state == "not_applicable"
                                else requirement.state
                            ),
                            "applicability_reason": False,
                        }
                    )
                else:
                    requirement._write_internal(
                        {
                            "mandatory": False,
                            "applicability": "not_applicable",
                            "state": "not_applicable",
                            "applicability_reason": _(
                                "Not applicable for the selected certification "
                                "entry mode."
                            ),
                        }
                    )

    def action_start(self):
        """Start a certification dossier and instantiate its stage checklist."""
        for process in self:
            process._check_process_access()
            process._check_certification_entry()
            if process.started_at:
                continue
            if len(process.stage_ids) != len(_PROCESS_STAGES):
                raise ValidationError(_("The six official stages are required."))
            process._write_internal({"started_at": fields.Datetime.now()})
            process._record_event(
                "process_started",
                to_state="in_progress",
                details={"stage_count": len(_PROCESS_STAGES)},
            )
            process.action_refresh_readiness()
        return True

    def action_start_assigned_test_set_cycle(self):
        """Start from the SII-assigned set without creating another postulation."""
        self.ensure_one()
        self._check_process_access()
        if self.started_at:
            raise UserError(_("This certification process has already started."))
        if self.process_mode not in {"new_postulation", "assigned_test_set"}:
            raise UserError(
                _("This action is only for an SII-assigned test-set dossier.")
            )
        self._validate_assigned_test_set_entry()
        if self.process_mode == "new_postulation":
            entry_requirements = self._entry_requirements()
            if self.sii_application_reference or entry_requirements.mapped(
                "evidence_ids"
            ):
                raise UserError(
                    _(
                        "This dossier already contains postulation evidence or a "
                        "reference. Continue with its existing SII postulation path."
                    )
                )
            self.write({"process_mode": "assigned_test_set"})
        return self.action_start()

    def action_record_postulation_acceptance(self):
        """Open the controlled capture of the SII postulation acceptance."""
        self.ensure_one()
        self._check_process_access()
        assigned_stage = self.stage_ids.filtered(
            lambda item: item.code == "assigned_set"
        )
        if self.process_mode != "new_postulation":
            raise UserError(
                _("This process uses existing-authorization evidence, not postulation.")
            )
        requirement = self._ensure_postulation_requirement()
        is_legacy_assigned_stage = (
            self.started_at
            and self.current_stage_code == "assigned_set"
            and assigned_stage.state == "pending"
            and requirement.state == "pending"
        )
        if self.started_at and not is_legacy_assigned_stage:
            raise UserError(
                _("Postulation acceptance must be recorded before starting.")
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Record SII Postulation Acceptance"),
            "res_model": "l10n_cl.dte.certification.postulation.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_process_id": self.id},
        }

    def action_record_existing_authorization(self):
        """Open evidence capture for an already authorized SII taxpayer."""
        self.ensure_one()
        self._check_process_access()
        if self.process_mode != "authorized_taxpayer":
            raise UserError(
                _("Choose the already-authorized taxpayer mode for this action.")
            )
        if self.started_at:
            raise UserError(
                _("Existing-authorization evidence must be recorded before starting.")
            )
        requirement = self._entry_requirements().filtered(
            lambda item: item.code == "existing_authorization"
        )
        if len(requirement) != 1:
            raise ValidationError(
                _("The existing-authorization requirement is missing.")
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Record Existing SII Authorization"),
            "res_model": "l10n_cl.dte.certification.authorization.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_process_id": self.id},
        }

    def action_refresh_readiness(self):
        """Refresh local readiness checks without changing fiscal evidence."""
        for process in self:
            process._check_process_access()
            diagnostics = process._check_company_readiness()
            for certification_set in process.certification_set_ids:
                if certification_set.state != "ready":
                    diagnostics.append(
                        {"set_id": certification_set.id, "code": "set_not_ready"}
                    )
            process._write_internal({"readiness_diagnostics": diagnostics})
            process._record_event(
                "readiness_refreshed",
                details={"diagnostic_count": len(diagnostics)},
            )
        return True

    def action_complete_stage(self):
        """Open the next stage after its predecessor has been accepted."""
        for process in self:
            process._check_process_access()
            stage = next(
                (
                    item
                    for item in process.stage_ids
                    if item.code == process.current_stage_code
                ),
                self.env["l10n_cl.dte.certification.process.stage"],
            )
            if not stage:
                if process.state != "completed":
                    raise UserError(
                        _(
                            "All stages must be accepted with verified "
                            "registration evidence."
                        )
                    )
                return {"type": "ir.actions.act_window_close"}
            return {
                "type": "ir.actions.act_window",
                "name": _("Current Certification Stage"),
                "res_model": stage._name,
                "res_id": stage.id,
                "view_mode": "form",
                "target": "current",
            }

    def action_reopen_stage(self):
        """Reopen a rejected stage while retaining all historical evidence."""
        self.ensure_one()
        self._check_process_access()
        return {
            "type": "ir.actions.act_window",
            "name": _("Reopen Certification Stage"),
            "res_model": "l10n_cl.dte.certification.reopen.stage.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_process_id": self.id},
        }

    def _record_event(
        self, event_code, stage=None, from_state=None, to_state=None, details=None
    ):
        """Append an immutable process event with actor and correlation data."""
        self.ensure_one()
        details = details or {}
        return (
            self.env["l10n_cl.dte.certification.process.event"]
            .sudo()
            ._create_from_transition(
                [
                    {
                        "process_id": self.id,
                        "stage_id": stage.id if stage else False,
                        "event_code": event_code,
                        "occurred_at": fields.Datetime.now(),
                        "user_id": self.env.uid,
                        "from_state": from_state or False,
                        "to_state": to_state or False,
                        "external_reference": details.get("external_reference"),
                        "correlation_id": details.get("correlation_id"),
                        "payload_digest": details.get("payload_digest"),
                        "details": details,
                    }
                ]
            )
        )

    def action_archive(self):
        """Archive a completed or abandoned dossier without deleting history."""
        self._check_process_access()
        for process in self:
            if process.state not in {"completed", "blocked"}:
                raise UserError(
                    _(
                        "Only a completed or blocked certification process "
                        "can be archived."
                    )
                )
            process._write_internal({"active": False})
            process._record_event("process_archived", from_state=process.state)
        return True

    @api.model
    def _check_manager(self):
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(
                _("Only a DTE manager can manage certification processes.")
            )
