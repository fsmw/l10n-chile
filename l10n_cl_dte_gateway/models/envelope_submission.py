# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib

import psycopg2

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class SiiEnvelopeSubmission(models.Model):
    _name = "l10n_cl.sii.envelope.submission"
    _description = "SII Multi-DTE Envelope Submission"
    _order = "create_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, readonly=True, copy=False, index=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        index=True,
        ondelete="restrict",
    )
    environment = fields.Selection(
        [("certification", "Certification"), ("production", "Production")],
        required=True,
        readonly=True,
        index=True,
    )
    request_xml = fields.Binary(
        required=True,
        attachment=True,
        readonly=True,
        copy=False,
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    request_filename = fields.Char(readonly=True, copy=False)
    request_digest = fields.Char(required=True, readonly=True, index=True, copy=False)
    idempotency_key = fields.Char(required=True, readonly=True, index=True, copy=False)
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("sending", "Sending"),
            ("submitted", "Received by SII"),
            ("processing", "Processing"),
            ("accepted", "Accepted"),
            ("rejected", "Rejected"),
            ("outcome_unknown", "Outcome Unknown"),
            ("error", "Transport Error"),
        ],
        required=True,
        default="pending",
        index=True,
        copy=False,
    )
    track_id = fields.Char(readonly=True, copy=False, index=True)
    submitted_at = fields.Datetime(readonly=True, copy=False)
    last_checked_at = fields.Datetime(readonly=True, copy=False)
    attempt_ids = fields.One2many("l10n_cl.sii.envelope.attempt", "submission_id")

    _sql_constraints = [
        (
            "envelope_idempotency_uniq",
            "unique(idempotency_key)",
            "Envelope key must be unique.",
        ),
        (
            "envelope_digest_company_uniq",
            "unique(company_id, environment, request_digest)",
            "This signed envelope is already registered.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super
        raise UserError(
            _("SII envelope submissions are created from a validated workflow.")
        )

    @api.model
    def _create_from_workflow(self, values):
        company = self.env["res.company"].browse(values.get("company_id")).exists()
        environment = values.get("environment")
        try:
            content = base64.b64decode(values.get("request_xml"), validate=True)
        except (TypeError, ValueError) as error:
            raise ValidationError(_("A signed envelope is required.")) from error
        digest = hashlib.sha256(content).hexdigest()
        if values.get("request_digest") != digest:
            raise ValidationError(_("The envelope digest does not match its bytes."))
        if not company or environment not in {"certification", "production"}:
            raise ValidationError(_("The envelope company or environment is invalid."))
        if company.dte_sii_environment != environment:
            raise ValidationError(_("The envelope mode differs from the company mode."))
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(_("Only a DTE manager can submit an SII envelope."))
        return super().create(values)

    def _write_internal(self, values):
        return super().write(values)

    def write(self, values):  # pylint: disable=method-required-super
        raise UserError(
            _("Envelope transmission state changes through gateway actions.")
        )

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("SII envelope history is retained as fiscal evidence."))

    def action_open(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("SII Envelope Submission"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_enqueue_send(self):
        self.ensure_one()
        if self.state != "pending":
            raise UserError(_("Only a pending envelope can be sent."))
        self._verify_request()
        gateway = self.env["l10n_cl.sii.gateway.factory"].get_envelope_gateway(
            self.environment
        )
        gateway.validate_configuration(self.company_id, self.environment)
        self.with_delay(identity_key=f"sii-envelope-{self.idempotency_key}")._job_send()
        return self.action_open()

    def _job_send(self):
        self.ensure_one()
        if self.state != "pending":
            return True
        self._verify_request()
        gateway = self.env["l10n_cl.sii.gateway.factory"].get_envelope_gateway(
            self.environment
        )
        attempt = self.env["l10n_cl.sii.envelope.attempt"]._create_for_operation(
            self, "send"
        )
        self._write_internal({"state": "sending"})
        try:
            result = gateway.send_envelope(
                self.company_id,
                self.environment,
                base64.b64decode(self.request_xml),
                self.idempotency_key,
            )
            response = result.get("raw_response")
            track_id = (result.get("track_id") or "").strip()
            if result.get("is_simulated"):
                raise ValidationError(
                    _("Certification and Production cannot accept simulated responses.")
                )
            if not isinstance(response, bytes) or not response or not track_id:
                raise ValidationError(
                    _("SII did not return a TrackID and response evidence.")
                )
            attempt._complete("completed", response)
            self._write_internal(
                {
                    "state": "submitted",
                    "track_id": track_id,
                    "submitted_at": fields.Datetime.now(),
                }
            )
        except psycopg2.Error:
            raise
        except Exception as error:
            attempt._complete("unknown", error_summary=str(error)[:1000])
            self._write_internal({"state": "outcome_unknown"})
        return True

    def action_enqueue_status_query(self):
        self.ensure_one()
        if (
            self.state not in {"submitted", "processing", "outcome_unknown"}
            or not self.track_id
        ):
            raise UserError(_("A TrackID is required to query SII status."))
        self.with_delay(
            identity_key=f"sii-envelope-query-{self.id}-{fields.Datetime.now()}"
        )._job_query()
        return True

    def _job_query(self):
        self.ensure_one()
        gateway = self.env["l10n_cl.sii.gateway.factory"].get_envelope_gateway(
            self.environment
        )
        attempt = self.env["l10n_cl.sii.envelope.attempt"]._create_for_operation(
            self, "status"
        )
        try:
            result = gateway.query_envelope(
                self.company_id, self.environment, self.track_id
            )
            response = result.get("raw_response")
            state = result.get("state")
            if result.get("is_simulated") or state not in {
                "processing",
                "accepted",
                "rejected",
            }:
                raise ValidationError(_("The SII status response is invalid."))
            if not isinstance(response, bytes) or not response:
                raise ValidationError(_("The SII status response has no evidence."))
            attempt._complete("completed", response)
            self._write_internal(
                {"state": state, "last_checked_at": fields.Datetime.now()}
            )
        except psycopg2.Error:
            raise
        except Exception as error:
            attempt._complete("unknown", error_summary=str(error)[:1000])
            self._write_internal({"state": "outcome_unknown"})
        return True

    def _verify_request(self):
        self.ensure_one()
        content = base64.b64decode(self.request_xml, validate=True)
        if hashlib.sha256(content).hexdigest() != self.request_digest:
            raise ValidationError(_("The stored SII envelope fingerprint changed."))


class SiiEnvelopeAttempt(models.Model):
    _name = "l10n_cl.sii.envelope.attempt"
    _description = "SII Envelope Transmission Attempt"
    _order = "started_at desc, id desc"

    submission_id = fields.Many2one(
        "l10n_cl.sii.envelope.submission",
        required=True,
        index=True,
        ondelete="restrict",
    )
    company_id = fields.Many2one(
        related="submission_id.company_id", store=True, index=True
    )
    operation = fields.Selection(
        [("send", "Send"), ("status", "Status Query")], required=True
    )
    started_at = fields.Datetime(required=True, readonly=True)
    finished_at = fields.Datetime(readonly=True)
    state = fields.Selection(
        [
            ("running", "Running"),
            ("completed", "Completed"),
            ("unknown", "Unknown"),
            ("failed", "Failed"),
        ],
        required=True,
        default="running",
        readonly=True,
    )
    request_digest = fields.Char(required=True, readonly=True)
    response_digest = fields.Char(readonly=True)
    response_xml = fields.Binary(
        attachment=True,
        readonly=True,
        copy=False,
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    error_summary = fields.Char(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super
        raise UserError(_("Gateway attempts are created by transmission actions."))

    @api.model
    def _create_for_operation(self, submission, operation):
        return super().create(
            {
                "submission_id": submission.id,
                "operation": operation,
                "started_at": fields.Datetime.now(),
                "request_digest": submission.request_digest,
            }
        )

    def _complete(self, state, response=None, error_summary=False):
        self.ensure_one()
        values = {
            "state": state,
            "finished_at": fields.Datetime.now(),
            "error_summary": error_summary,
        }
        if response:
            values.update(
                {
                    "response_xml": base64.b64encode(response),
                    "response_digest": hashlib.sha256(response).hexdigest(),
                }
            )
        return super().write(values)

    def write(self, values):  # pylint: disable=method-required-super
        raise UserError(_("SII envelope attempts are immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("SII envelope attempts are retained as fiscal evidence."))
