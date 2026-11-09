# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_STAGE_PROTECTED_FIELDS = {
    "state",
    "started_at",
    "submitted_at",
    "decided_at",
    "external_reference",
    "submission_channel",
    "submitted_payload_digest",
    "sii_status",
    "sii_message",
    "diagnostics",
}


class DteCertificationProcessStage(models.Model):
    _name = "l10n_cl.dte.certification.process.stage"
    _description = "SII DTE Certification Process Stage"
    _order = "sequence, id"
    _check_company_auto = True
    _sql_constraints = [
        (
            "process_stage_code_uniq",
            "unique(process_id, code)",
            "Each certification process contains each official stage once.",
        ),
    ]

    process_id = fields.Many2one(
        "l10n_cl.dte.certification.process",
        required=True,
        index=True,
        check_company=True,
        ondelete="restrict",
    )
    company_id = fields.Many2one(
        related="process_id.company_id", store=True, index=True
    )
    code = fields.Selection(
        [
            ("assigned_set", "Assigned Test Set"),
            ("simulation_set", "Simulation Set"),
            ("information_exchange", "Information Exchange"),
            ("printed_samples", "Printed Samples"),
            ("compliance_declaration", "Compliance Declaration"),
            ("issuer_registration", "Electronic Issuer Registration"),
        ],
        required=True,
        index=True,
    )
    sequence = fields.Integer(required=True, readonly=True)
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("in_progress", "In Progress"),
            ("ready", "Ready"),
            ("submitted", "Submitted"),
            ("waiting_sii", "Waiting for SII"),
            ("accepted", "Accepted by SII"),
            ("rejected", "Rejected by SII"),
            ("blocked", "Blocked"),
        ],
        required=True,
        default="pending",
        readonly=True,
        index=True,
    )
    started_at = fields.Datetime(readonly=True, copy=False)
    submitted_at = fields.Datetime(readonly=True, copy=False)
    decided_at = fields.Datetime(readonly=True, copy=False)
    external_reference = fields.Char(readonly=True, copy=False, index=True)
    submission_channel = fields.Selection(
        [("sii_portal", "SII Portal"), ("sii_webservice", "SII Web Service")],
        readonly=True,
        copy=False,
    )
    submitted_payload_digest = fields.Char(readonly=True, copy=False, index=True)
    sii_status = fields.Char(readonly=True, copy=False)
    sii_message = fields.Text(readonly=True, copy=False)
    source_document_version = fields.Char(readonly=True, copy=False)
    source_url = fields.Char(readonly=True, copy=False)
    requirement_ids = fields.One2many(
        "l10n_cl.dte.certification.process.requirement", "stage_id", copy=False
    )
    evidence_ids = fields.One2many(
        "l10n_cl.dte.certification.evidence", "stage_id", copy=False
    )
    certification_set_id = fields.Many2one(
        "l10n_cl.dte.certification.set",
        check_company=True,
        ondelete="restrict",
        copy=False,
    )
    certification_run_ids = fields.Many2many(
        "l10n_cl.dte.certification.run",
        relation="l10n_cl_cert_stage_run_rel",
        column1="stage_id",
        column2="run_id",
        copy=False,
    )
    electronic_submission_ids = fields.Many2many(
        "l10n_cl.electronic.submission",
        relation="l10n_cl_cert_stage_submission_rel",
        column1="stage_id",
        column2="submission_id",
        copy=False,
    )
    sii_envelope_submission_ids = fields.Many2many(
        "l10n_cl.sii.envelope.submission",
        relation="l10n_cl_cert_stage_envelope_rel",
        column1="stage_id",
        column2="submission_id",
        copy=False,
    )
    report_sample_package_id = fields.Many2one(
        "l10n_cl.dte.report.sample.package",
        string="SII Print Sample Package",
        check_company=True,
        ondelete="restrict",
        copy=False,
    )
    diagnostics = fields.Json(readonly=True, copy=False)
    can_record_existing_sii_result = fields.Boolean(
        compute="_compute_can_record_existing_sii_result"
    )

    @api.depends(
        "state",
        "code",
        "certification_set_id",
        "certification_run_ids",
        "sii_envelope_submission_ids.state",
        "sii_envelope_submission_ids.result_counts",
    )
    def _compute_can_record_existing_sii_result(self):
        for stage in self:
            stage.can_record_existing_sii_result = bool(
                stage.state == "ready" and stage._has_accepted_assigned_set_envelope()
            )

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super
        raise AccessError(_("Certification stages are created with their process."))

    @api.model
    def _create_from_process(self, vals):
        process = (
            self.env["l10n_cl.dte.certification.process"]
            .browse(vals.get("process_id"))
            .exists()
        )
        if len(process) != 1:
            raise ValidationError(_("A valid certification process is required."))
        process._check_process_access()
        values = vals.copy()
        values["state"] = "pending"
        return super(DteCertificationProcessStage, self.sudo()).create(values)

    @api.constrains(
        "certification_set_id",
        "certification_run_ids",
        "electronic_submission_ids",
        "sii_envelope_submission_ids",
        "report_sample_package_id",
    )
    def _check_stage_links(self):
        for stage in self:
            if stage.certification_set_id and (
                stage.certification_set_id.company_id != stage.company_id
                or stage.certification_set_id.sii_environment != "certification"
            ):
                raise ValidationError(
                    _("A stage can only link a Certification set for its company.")
                )
            if any(
                run.set_id.company_id != stage.company_id
                or run.set_id.sii_environment != "certification"
                for run in stage.certification_run_ids
            ):
                raise ValidationError(
                    _("A stage can only link Certification executions for its company.")
                )
            if any(
                submission.company_id != stage.company_id
                or submission.environment != "certification"
                for submission in stage.sii_envelope_submission_ids
            ):
                raise ValidationError(
                    _(
                        "A stage can only link Certification envelope submissions "
                        "for its company."
                    )
                )
            if stage.report_sample_package_id and (
                stage.code != "printed_samples"
                or stage.report_sample_package_id.company_id != stage.company_id
                or stage.report_sample_package_id.state != "generated"
            ):
                raise ValidationError(
                    _(
                        "A print-sample stage requires a generated package for "
                        "the same company."
                    )
                )
            if any(
                submission.company_id != stage.company_id
                or submission.environment != "certification"
                for submission in stage.electronic_submission_ids
            ):
                raise ValidationError(
                    _(
                        "A stage can only link Certification submissions "
                        "for its company."
                    )
                )

    def _check_stage_access(self):
        """Revalidate access to the parent process and stage evidence."""
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(_("Only a DTE manager can operate certification stages."))
        for stage in self:
            stage.process_id._check_process_access()

    def action_begin(self):
        """Begin this stage only after the previous stage is accepted."""
        for stage in self:
            stage._check_stage_access()
            process = stage.process_id
            if not process.started_at:
                raise UserError(_("Start the certification process first."))
            if process.current_stage_code != stage.code or stage.state != "pending":
                raise UserError(
                    _("Only the first pending stage can start after prior acceptance.")
                )
            if stage.code == "assigned_set":
                process._check_certification_entry()
            stage._write_internal(
                {"state": "in_progress", "started_at": fields.Datetime.now()}
            )
            process._record_event(
                "stage_started",
                stage=stage,
                from_state="pending",
                to_state="in_progress",
            )
        return True

    def action_run_preflight(self):
        """Run the stage-specific, versioned readiness checklist."""
        for stage in self:
            stage._check_stage_access()
            if stage.state not in {"in_progress", "blocked"}:
                raise UserError(_("Only an active stage can run its preflight."))
            diagnostics = []
            for requirement in stage.requirement_ids:
                if requirement.external_result:
                    continue
                if requirement.applicability == "not_applicable":
                    if requirement.mandatory or not requirement.applicability_reason:
                        diagnostics.append(
                            {
                                "requirement": requirement.code,
                                "code": "invalid_exclusion",
                            }
                        )
                elif requirement.mandatory and requirement.state not in {
                    "ready",
                    "accepted",
                }:
                    diagnostics.append(
                        {
                            "requirement": requirement.code,
                            "code": "requirement_incomplete",
                        }
                    )
                elif (
                    requirement.applicability == "conditional"
                    and requirement.state
                    not in {
                        "ready",
                        "accepted",
                        "not_applicable",
                    }
                ):
                    diagnostics.append(
                        {
                            "requirement": requirement.code,
                            "code": "applicability_unresolved",
                        }
                    )
            if stage.code in {"assigned_set", "simulation_set"}:
                if len(stage.certification_run_ids) != 1:
                    diagnostics.append({"code": "one_execution_required"})
                else:
                    stage.certification_run_ids.action_run_official_preflight()
                    diagnostics.extend(
                        stage.certification_run_ids.reportability_diagnostics or []
                    )
            stage._write_internal(
                {
                    "diagnostics": diagnostics,
                    "state": "blocked" if diagnostics else "ready",
                }
            )
            stage.process_id._record_event(
                "stage_preflight_completed",
                stage=stage,
                to_state=stage.state,
                details={"diagnostic_count": len(diagnostics)},
            )
        return True

    def action_submit(self):
        """Open the auditable manual Certification portal submission flow."""
        self.ensure_one()
        for stage in self:
            stage._check_stage_access()
            if stage.state != "ready":
                raise UserError(
                    _("Run and pass the stage preflight before submitting.")
                )
            if stage.code in {"assigned_set", "simulation_set"}:
                if len(stage.certification_run_ids) != 1:
                    raise ValidationError(_("Exactly one execution must be linked."))
                return {
                    "type": "ir.actions.act_window",
                    "name": _("Record SII Portal Upload"),
                    "res_model": "l10n_cl.dte.certification.submission.wizard",
                    "view_mode": "form",
                    "target": "new",
                    "context": {"default_stage_id": stage.id},
                }
            elif stage.code in {"information_exchange", "printed_samples"}:
                if stage.code == "information_exchange":
                    raise UserError(
                        _(
                            "Information Exchange requires verified incoming DTEs "
                            "and submitted responses before portal evidence can be "
                            "recorded."
                        )
                    )
                if not stage.report_sample_package_id:
                    raise ValidationError(
                        _("Link a generated print-sample package before submitting.")
                    )
                return stage._open_portal_action_wizard()
            else:
                return stage._open_portal_action_wizard()
        return True

    def _open_portal_action_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Record SII Portal Action"),
            "res_model": "l10n_cl.dte.certification.stage.submission.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_stage_id": self.id,
                "default_evidence_type": {
                    "printed_samples": "printed_sample_pdf",
                    "compliance_declaration": "compliance_declaration",
                    "issuer_registration": "sii_upload_receipt",
                }.get(self.code, "other"),
            },
        }

    def action_poll_result(self):
        """Query the SII status for a previously submitted operation."""
        for stage in self:
            stage._check_stage_access()
            if stage.state not in {"submitted", "waiting_sii"}:
                raise UserError(_("Only submitted stages can be queried."))
            if (
                not stage.electronic_submission_ids
                and not stage.sii_envelope_submission_ids
            ):
                raise UserError(
                    _(
                        "This stage has no automatic SII submission; register "
                        "its portal result."
                    )
                )
            for submission in stage.electronic_submission_ids:
                submission.action_enqueue_status_query()
            for submission in stage.sii_envelope_submission_ids:
                submission.action_enqueue_status_query()
            stage._write_internal({"state": "waiting_sii"})
            stage.process_id._record_event(
                "stage_status_query_enqueued", stage=stage, to_state="waiting_sii"
            )
        return True

    def action_open_evidence_wizard(self):
        """Open the controlled evidence upload wizard for this stage."""
        self.ensure_one()
        self._check_stage_access()
        return {
            "type": "ir.actions.act_window",
            "name": _("Attach Certification Evidence"),
            "res_model": "l10n_cl.dte.certification.evidence.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_stage_id": self.id},
        }

    def action_register_external_result(self):
        """Store a verified external response without replacing its original."""
        self.ensure_one()
        self._check_stage_access()
        return self.action_reconcile()

    def action_accept_manual_result(self):
        """Record a portal result with source, reference, date and reviewer."""
        self.ensure_one()
        self._check_stage_access()
        can_record_existing_result = (
            self.state == "ready" and self._has_accepted_assigned_set_envelope()
        )
        if self.state not in {"submitted", "waiting_sii", "rejected"} and not (
            can_record_existing_result
        ):
            raise UserError(
                _(
                    "Submit this stage first, or link the already accepted "
                    "assigned-set "
                    "envelope before recording its SII review result."
                )
            )
        requirements = self.requirement_ids.filtered(
            lambda item: (
                item.external_result
                and item.state == "pending"
                and item.code not in {"accepted_postulation", "existing_authorization"}
            )
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Register SII Result"),
            "res_model": "l10n_cl.dte.certification.manual.result.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_stage_id": self.id,
                "default_requirement_id": requirements.id
                if len(requirements) == 1
                else False,
            },
        }

    def _has_accepted_assigned_set_envelope(self):
        """Check if this stage links the complete run and its accepted SII envelope."""
        self.ensure_one()
        if (
            self.code != "assigned_set"
            or not self.certification_set_id
            or len(self.certification_run_ids) != 1
        ):
            return False
        run, submission = self.certification_set_id._get_sii_advance_result()
        return bool(
            run
            and submission
            and run in self.certification_run_ids
            and submission in self.sii_envelope_submission_ids
        )

    def action_reject(self):
        """Record an SII rejection while retaining its response and prior attempts."""
        self.ensure_one()
        self._check_stage_access()
        if self.state not in {"submitted", "waiting_sii"}:
            raise UserError(_("Only a submitted stage can be rejected by SII."))
        previous = self.state
        self._write_internal(
            {
                "state": "rejected",
                "decided_at": fields.Datetime.now(),
                "sii_status": "rejected",
            }
        )
        self.process_id._record_event(
            "stage_rejected",
            stage=self,
            from_state=previous,
            to_state="rejected",
        )
        return True

    def action_reconcile(self):
        """Compare SII result counts and DTE outcomes with the submitted envelope."""
        for stage in self:
            stage._check_stage_access()
            submissions = stage.electronic_submission_ids
            envelopes = stage.sii_envelope_submission_ids
            if not submissions and not envelopes:
                raise UserError(_("No SII submissions are linked to this stage."))
            statuses = set(submissions.mapped("state")) | set(envelopes.mapped("state"))
            if statuses.intersection({"pending", "sending", "submitted", "processing"}):
                stage._write_internal({"state": "waiting_sii"})
                continue
            if statuses.intersection({"outcome_unknown", "error"}):
                stage._write_internal(
                    {"state": "blocked", "sii_status": "outcome_unknown"}
                )
                continue
            if envelopes:
                if any(envelope.state == "rejected" for envelope in envelopes):
                    stage._write_internal(
                        {
                            "state": "rejected",
                            "sii_status": "envelope_rejected",
                            "decided_at": fields.Datetime.now(),
                        }
                    )
                else:
                    stage._write_internal(
                        {
                            "state": "blocked",
                            "sii_status": "per_document_results_required",
                            "diagnostics": [
                                {"code": "envelope_receipt_is_not_dte_acceptance"}
                            ],
                        }
                    )
                continue
            documents = submissions.mapped("document_id")
            if not documents or any(
                document.fiscal_state != "accepted" for document in documents
            ):
                stage._write_internal(
                    {
                        "state": "rejected",
                        "sii_status": "dte_not_accepted",
                        "decided_at": fields.Datetime.now(),
                    }
                )
                continue
            if not stage._external_result_is_verified():
                stage._write_internal(
                    {
                        "state": "blocked",
                        "sii_status": "external_result_evidence_required",
                        "diagnostics": [{"code": "verified_external_result_required"}],
                    }
                )
                continue
            stage._write_internal(
                {
                    "state": "accepted",
                    "sii_status": "accepted",
                    "decided_at": fields.Datetime.now(),
                }
            )
            stage.process_id._record_event(
                "stage_accepted_by_sii",
                stage=stage,
                from_state="waiting_sii",
                to_state="accepted",
            )
        return True

    def _external_result_is_verified(self):
        """Require the stage's designated external decision before acceptance."""
        self.ensure_one()
        requirements = self.requirement_ids.filtered("external_result")
        requirements = requirements.filtered(
            lambda item: item.applicability != "not_applicable"
        )

        def has_verified_evidence(requirement):
            return requirement.state == "accepted" and any(
                evidence.verification_state == "source_verified"
                and evidence.requirement_id == requirement
                for evidence in requirement.evidence_ids
            )

        return all(has_verified_evidence(requirement) for requirement in requirements)

    def write(self, vals):
        """Reject direct edits to state and SII result fields."""
        self._check_stage_access()
        if _STAGE_PROTECTED_FIELDS.intersection(vals):
            raise UserError(
                _("Stage results can only change through workflow actions.")
            )
        if set(vals) - {
            "certification_set_id",
            "certification_run_ids",
            "sii_envelope_submission_ids",
            "source_document_version",
            "source_url",
            "report_sample_package_id",
        }:
            raise UserError(_("Only pre-submission stage references may be changed."))
        if any(
            stage.state not in {"pending", "in_progress", "blocked"} for stage in self
        ):
            raise UserError(_("Stage references are frozen after submission."))
        return super().write(vals)

    def _write_internal(self, vals):
        """Update stage state only from an authorized transition service."""
        return super().write(vals)

    def unlink(self):  # pylint: disable=method-required-super
        """Keep historical stage records."""
        raise UserError(_("Certification stages are retained as audit evidence."))
