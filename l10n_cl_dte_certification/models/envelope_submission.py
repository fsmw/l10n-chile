# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SiiEnvelopeSubmission(models.Model):
    _inherit = "l10n_cl.sii.envelope.submission"

    certification_run_id = fields.Many2one(
        "l10n_cl.dte.certification.run",
        index=True,
        readonly=True,
        ondelete="restrict",
    )
    process_stage_id = fields.Many2one(
        "l10n_cl.dte.certification.process.stage",
        index=True,
        readonly=True,
        ondelete="restrict",
    )

    def _write_internal(self, values):
        result = super()._write_internal(values)
        for submission in self.filtered("certification_run_id"):
            if submission.state in {"pending", "sending", "error", "outcome_unknown"}:
                state = {
                    "pending": "queued",
                    "sending": "sending",
                    "error": "transport_error",
                    "outcome_unknown": "outcome_unknown",
                }[submission.state]
                submission.certification_run_id._write_internal(
                    {"reportability_state": state}
                )
            elif submission.state in {"submitted", "processing"}:
                submission.certification_run_id._write_internal(
                    {"reportability_state": "submitted"}
                )
            elif submission.state in {
                "accepted",
                "accepted_with_observations",
                "partially_accepted",
                "rejected",
            }:
                submission.certification_run_id._write_internal(
                    {"reportability_state": "reconciled"}
                )
        return result

    @api.model
    def _create_for_run(self, run):
        run.ensure_one()
        if run.reportability_state != "ready" or run.state != "exported":
            raise UserError(_("Official preflight must pass before envelope creation."))
        if not run.envelope_file or not run.envelope_digest:
            raise UserError(_("An exported signed envelope is required."))
        content = base64.b64decode(run.envelope_file)
        return self._create_from_workflow(
            {
                "name": run.envelope_filename or run.name,
                "company_id": run.company_id.id,
                "environment": "certification",
                "request_xml": run.envelope_file,
                "request_filename": run.envelope_filename,
                "request_digest": hashlib.sha256(content).hexdigest(),
                "idempotency_key": uuid.uuid4().hex,
                "certification_run_id": run.id,
                "process_stage_id": run.process_stage_id.id,
            }
        )


class DteCertificationRun(models.Model):
    _inherit = "l10n_cl.dte.certification.run"

    envelope_submission_ids = fields.One2many(
        "l10n_cl.sii.envelope.submission",
        "certification_run_id",
        readonly=True,
    )
    envelope_submission_count = fields.Integer(
        compute="_compute_envelope_submission_count"
    )
    can_prepare_sii_submission = fields.Boolean(
        compute="_compute_envelope_submission_count"
    )
    can_query_sii_status = fields.Boolean(compute="_compute_envelope_submission_count")
    can_correct_sii_envelope = fields.Boolean(
        compute="_compute_envelope_submission_count"
    )
    sii_response_code = fields.Char(
        string="Last SII Response Code",
        compute="_compute_sii_response_details",
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    sii_response_detail = fields.Text(
        string="Original SII Message",
        compute="_compute_sii_response_details",
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    sii_response_notice = fields.Text(
        string="SII Response Summary",
        compute="_compute_sii_response_details",
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    sii_response_warning = fields.Text(
        string="Response Verification",
        compute="_compute_sii_response_details",
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    sii_response_received_at = fields.Datetime(
        string="Response Received At",
        compute="_compute_sii_response_details",
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    sii_reported_rejection = fields.Boolean(
        compute="_compute_sii_response_details",
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    sii_track_id = fields.Char(
        string="TrackID",
        compute="_compute_sii_response_details",
        groups="l10n_cl_dte_core.group_dte_manager",
    )

    @api.depends(
        "envelope_submission_ids.response_code",
        "envelope_submission_ids.response_detail",
        "envelope_submission_ids.response_notice",
        "envelope_submission_ids.response_warning",
        "envelope_submission_ids.response_received_at",
        "envelope_submission_ids.reported_rejection",
        "envelope_submission_ids.track_id",
    )
    def _compute_sii_response_details(self):
        for run in self:
            latest = run.envelope_submission_ids[:1]
            run.sii_response_code = latest.response_code
            run.sii_response_detail = latest.response_detail
            run.sii_response_notice = latest.response_notice
            run.sii_response_warning = latest.response_warning
            run.sii_response_received_at = latest.response_received_at
            run.sii_reported_rejection = latest.reported_rejection
            run.sii_track_id = latest.track_id

    def _blocks_new_execution(self):
        if not super()._blocks_new_execution():
            return False
        latest = self.envelope_submission_ids[:1]
        if not latest or latest.request_digest != self.envelope_digest:
            return True
        try:
            self._check_execution_access()
            for submission in self.envelope_submission_ids:
                submission._verified_terminal_outcome()
        except (UserError, ValueError, TypeError):
            return True
        return False

    @api.depends(
        "envelope_submission_ids",
        "envelope_submission_ids.state",
        "envelope_submission_ids.track_id",
        "envelope_submission_ids.request_digest",
        "envelope_submission_ids.attempt_ids",
        "envelope_submission_ids.attempt_ids.finished_at",
        "envelope_submission_ids.attempt_ids.operation",
        "envelope_submission_ids.attempt_ids.response_digest",
        "envelope_submission_ids.attempt_ids.state",
        "envelope_digest",
        "can_operate",
        "set_id.parse_state",
        "set_id.case_coverage_state",
    )
    def _compute_envelope_submission_count(self):
        for run in self:
            run.envelope_submission_count = len(run.envelope_submission_ids)
            latest = run.envelope_submission_ids[:1]
            rejected = False
            set_is_ready = (
                run.set_id.parse_state == "parsed"
                and run.set_id.case_coverage_state == "complete"
            )
            if latest and run.can_operate and set_is_ready:
                try:
                    rejected = all(
                        submission._verified_schema_rejection()
                        for submission in run.envelope_submission_ids
                    )
                except (UserError, ValueError, TypeError):
                    rejected = False
            run.can_correct_sii_envelope = bool(
                rejected and latest.request_digest == run.envelope_digest
            )
            run.can_prepare_sii_submission = bool(
                run.can_operate
                and set_is_ready
                and (
                    not latest
                    or (rejected and latest.request_digest != run.envelope_digest)
                )
            )
            run.can_query_sii_status = bool(
                run.can_operate
                and set_is_ready
                and latest.track_id
                and latest.state in {"submitted", "processing", "outcome_unknown"}
            )

    def action_correct_sii_envelope(self):
        from ..services.envelope_correction import CertificationEnvelopeCorrection

        CertificationEnvelopeCorrection().prepare(self)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Corrected SII Envelope"),
                "message": _(
                    "The corrected envelope is ready for validation. "
                    "Original DTE, folios and SII evidence were retained."
                ),
                "type": "success",
                "sticky": True,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_query_sii_status(self):
        self.ensure_one()
        self._check_execution_access()
        if not self.can_query_sii_status:
            raise UserError(
                _("This execution has no SII TrackID eligible for a status query.")
            )
        self.envelope_submission_ids[:1].action_enqueue_status_query()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("SII Status Query"),
                "message": _(
                    "The status query was queued. "
                    "Refresh the SII response after processing."
                ),
                "type": "info",
                "sticky": False,
            },
        }

    def action_open_sii_submission(self):
        self.ensure_one()
        self._check_execution_access()
        if not self.envelope_submission_ids:
            raise UserError(_("This execution has no SII envelope submission yet."))
        action = self.envelope_submission_ids[:1].action_open()
        action["target"] = "new"
        return action
