import base64
import hashlib
from datetime import timedelta

import psycopg2

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import SQL

from ..services.certification_auth import SiiTransportError
from ..services.document_validation import (
    get_document_validator,
    validate_document_payload,
)
from ..services.production_response_parser import upload_status_message

POLL_BASE_DELAY = timedelta(minutes=5)
POLL_MAX_DELAY = timedelta(hours=6)
POLL_MAX_EXPONENT = 10
POLL_BATCH_SIZE = 200
POLL_SEARCH_FACTOR = 5
# After a month without a final answer the DTE needs a person, not a poller.
POLL_MAX_AGE = timedelta(days=30)


class ElectronicSubmission(models.Model):
    _name = "l10n_cl.electronic.submission"
    _description = "DTE SII Submission"
    _order = "create_date desc, id desc"
    _check_company_auto = True
    _sql_constraints = [
        (
            "submission_document_uniq",
            "unique(document_id)",
            "A DTE has one submission record.",
        ),
        (
            "submission_idempotency_uniq",
            "unique(idempotency_key)",
            "Submission key must be unique.",
        ),
    ]

    document_id = fields.Many2one(
        "l10n_cl.electronic.document", required=True, index=True, ondelete="restrict"
    )
    company_id = fields.Many2one(
        related="document_id.company_id", store=True, index=True
    )
    environment = fields.Selection(
        [
            ("demo", "Demo / Simulated"),
            ("certification", "Certification"),
            ("production", "Production"),
        ],
        required=True,
        readonly=True,
    )
    is_simulated = fields.Boolean(compute="_compute_is_simulated", store=True)
    demo_scenario = fields.Selection(
        [
            ("accepted", "Accepted"),
            ("rejected", "Rejected"),
            ("processing_then_accepted", "Processing then Accepted"),
            ("unknown_then_reconciled", "Unknown then Reconciled"),
        ],
        default="accepted",
        required=True,
        copy=False,
    )
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("sending", "Sending"),
            ("submitted", "Submitted"),
            ("processing", "Processing"),
            ("accepted", "Accepted"),
            ("rejected", "Rejected"),
            ("outcome_unknown", "Outcome Unknown"),
            ("error", "Transport Error"),
        ],
        default="pending",
        required=True,
        index=True,
        copy=False,
    )
    track_id = fields.Char(index=True, copy=False)
    idempotency_key = fields.Char(required=True, index=True, copy=False)
    submitted_at = fields.Datetime(copy=False)
    last_checked_at = fields.Datetime(copy=False)
    sii_has_observations = fields.Boolean(
        string="Accepted with Observations",
        readonly=True,
        copy=False,
        help="SII accepted the DTE with observations (reparos).",
    )
    sii_message = fields.Text(
        string="SII Message",
        readonly=True,
        copy=False,
        help="Last diagnostic or observation returned by SII or by the "
        "pre-dispatch checks.",
    )
    attempt_ids = fields.One2many("l10n_cl.submission.attempt", "submission_id")

    @api.depends("environment")
    def _compute_is_simulated(self):
        for submission in self:
            submission.is_simulated = submission.environment == "demo"

    @api.constrains("environment", "demo_scenario")
    def _check_demo_configuration(self):
        for submission in self:
            if (
                submission.environment != "demo"
                and submission.demo_scenario != "accepted"
            ):
                raise ValidationError(
                    _("Demo response scenarios can only be set for Demo submissions.")
                )

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            document = self.env["l10n_cl.electronic.document"].browse(
                values.get("document_id")
            )
            if not document or not document.exists():
                raise ValidationError(_("A DTE is required for an SII submission."))
            environment = values.get("environment") or document.sii_environment
            if environment != document.sii_environment:
                raise ValidationError(
                    _("The submission environment must match the immutable DTE mode.")
                )
            values["environment"] = environment
        return super().create(vals_list)

    def _gateway(self):
        self.ensure_one()
        if self.environment != self.document_id.sii_environment:
            raise ValidationError(
                _("The submission environment no longer matches its DTE snapshot.")
            )
        return self.env["l10n_cl.sii.gateway.factory"].get_gateway(self.environment)

    def write(self, vals):  # pylint: disable=method-required-super
        if set(vals) != {"demo_scenario"}:
            raise UserError(
                _("Submission state can only change through workflow actions.")
            )
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(_("Only a DTE manager can select a Demo scenario."))
        for submission in self:
            if submission.environment != "demo" or submission.state != "pending":
                raise UserError(
                    _("The Demo scenario can only be changed before a Demo send.")
                )
            if submission.attempt_ids:
                raise UserError(
                    _("A Demo scenario is immutable after the first attempt.")
                )
        return super().write(vals)

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("Submission records are retained as audit evidence."))

    def _write_internal(self, values):
        return super().write(values)

    def _dispatched_send_attempts(self):
        """Send attempts that may have reached SII; failed ones never left Odoo."""
        return self.attempt_ids.filtered(
            lambda attempt: attempt.operation == "send" and attempt.state != "failed"
        )

    def _was_never_dispatched(self):
        """Return whether every send attempt failed before reaching SII."""
        self.ensure_one()
        return not self._dispatched_send_attempts()

    def action_enqueue_submission(self):
        """Schedule one submission using a durable OCA queue job."""
        self.ensure_one()
        if self.state == "error" and self._was_never_dispatched():
            # Nothing reached SII, so the corrected DTE can be queued again.
            self._write_internal({"state": "pending"})
        if self.state != "pending":
            if self.state in {
                "sending",
                "submitted",
                "processing",
                "accepted",
                "rejected",
                "outcome_unknown",
            }:
                return True
            raise UserError(_("Only a pending SII submission can be enqueued."))
        self._gateway()
        document = self.document_id
        signed = document._get_signed_artifact()
        if len(signed) != 1:
            raise ValidationError(
                _("A submission needs exactly one signed DTE artifact.")
            )
        signed.action_verify_integrity()
        self.with_delay(
            identity_key=f"dte-send-{self.idempotency_key}"
        )._job_send_to_sii()
        return True

    def _job_send_to_sii(self):
        """Send once and persist raw response and TrackID where available."""
        self.ensure_one()
        self.env.cr.execute(
            SQL(
                "SELECT state FROM %s WHERE id = %s FOR UPDATE",
                SQL.identifier(self._table),
                self.id,
            )
        )
        self.invalidate_recordset(["state"])
        if self.state != "pending":
            return True
        gateway = self._gateway()
        document = self.document_id
        try:
            request_bytes = self._prepare_send_request(gateway)
        except (UserError, ValidationError) as error:
            # Nothing left this server: keep a retryable error, not ambiguity.
            return self._record_send_error(error)
        self._write_internal({"state": "sending", "sii_message": False})
        attempt = self.env["l10n_cl.submission.attempt"].create(
            {
                "submission_id": self.id,
                "operation": "send",
                "request_digest": hashlib.sha256(request_bytes).hexdigest(),
            }
        )
        if self.environment == "production":
            return self._send_production_request(gateway, attempt, request_bytes)
        try:
            result = gateway.send_document(
                self.company_id,
                self.environment,
                request_bytes,
                self.idempotency_key,
                self.demo_scenario,
            )
            raw_response = result.get("raw_response")
            track_id = (result.get("track_id") or "").strip()
            if not isinstance(raw_response, bytes) or not raw_response:
                raise ValidationError(_("The SII adapter returned no raw response."))
            result_state = result.get("state")
            if self.is_simulated and result.get("is_simulated") is not True:
                raise ValidationError(
                    _(
                        "The Demo adapter returned a response without "
                        "its simulation marker."
                    )
                )
            if result_state not in {"submitted", "outcome_unknown"}:
                raise ValidationError(_("The SII adapter returned an invalid receipt."))
            if result_state == "submitted" and not track_id:
                raise ValidationError(_("The SII adapter returned an invalid receipt."))
            response = self._create_response_artifact("sii_response", raw_response)
            attempt._complete(
                "unknown" if result_state == "outcome_unknown" else "completed",
                response_digest=response.sha256,
            )
            if result_state == "outcome_unknown":
                self._write_internal({"state": "outcome_unknown"})
                document.action_record_transition(
                    "outcome_unknown",
                    "sii_submission_outcome_unknown",
                    correlation_key=self.idempotency_key,
                    artifact_id=response.id,
                )
                return True
            self._write_internal(
                {
                    "state": "submitted",
                    "track_id": track_id,
                    "submitted_at": fields.Datetime.now(),
                }
            )
            document.action_record_transition(
                "submitted", "sii_submission_received", artifact_id=response.id
            )
        except Exception as error:  # the adapter boundary must persist ambiguity
            if isinstance(error, psycopg2.Error):
                # Database transaction failures must escape so the worker can
                # roll back and retry the whole job. Treating a serialization
                # failure as a transport outcome would mask the retry signal and
                # could attempt to mutate an already completed audit record.
                raise
            outcome_unknown = bool(getattr(error, "outcome_unknown", True))
            attempt._complete(
                "unknown" if outcome_unknown else "failed",
                error_code=type(error).__name__[:128],
                error_summary=str(error)[:4000],
            )
            state = "outcome_unknown" if outcome_unknown else "error"
            self._write_internal({"state": state})
            if outcome_unknown:
                document.action_record_transition(
                    "outcome_unknown",
                    "sii_submission_outcome_unknown",
                    correlation_key=self.idempotency_key,
                )
        return True

    def _prepare_send_request(self, gateway):
        """Return the exact bytes to transmit; raise before any network access."""
        self.ensure_one()
        signed = self.document_id._get_signed_artifact()
        if len(signed) != 1:
            raise ValidationError(
                _("A submission needs exactly one signed DTE artifact.")
            )
        signed.action_verify_integrity()
        request_bytes = base64.b64decode(signed.content, validate=True)
        self._validate_document_payload(request_bytes)
        if self.environment != "production":
            return request_bytes
        gateway.validate_configuration(self.company_id, self.environment)
        envelope = self._prepare_production_request()
        return base64.b64decode(
            envelope.with_context(bin_size=False).content, validate=True
        )

    def _send_production_request(self, gateway, attempt, request_bytes):
        """Upload the bound envelope and apply its verified outcome."""
        self.ensure_one()
        try:
            result = gateway.send_envelope(
                self.company_id, self.environment, request_bytes, self.idempotency_key
            )
        except SiiTransportError as error:
            if not error.dispatched:
                return self._record_send_error(error, attempt)
            result = error
        except (UserError, ValidationError) as error:
            return self._record_send_error(error, attempt)
        except Exception as error:  # the upload may have reached SII
            if isinstance(error, psycopg2.Error):
                raise
            result = SiiTransportError(
                type(error).__name__, dispatched=True, code="unexpected_error"
            )
        try:
            self._apply_production_outcome(attempt, result, "send")
        except Exception as error:  # the upload already happened
            if isinstance(error, psycopg2.Error):
                raise
            return self._record_unverified_outcome(attempt, error)
        if self.state == "rejected":
            code = isinstance(result, dict) and result.get("transport_code")
            self._write_internal({"sii_message": upload_status_message(code)})
        elif self.state == "outcome_unknown":
            self._write_internal({"sii_message": attempt.error_summary})
        return True

    def _record_unverified_outcome(self, attempt, error):
        """Keep an upload whose result could not be applied as unknown."""
        message = str(error)[:4000]
        attempt.sudo()._complete(
            "unknown", error_code=type(error).__name__[:128], error_summary=message
        )
        self.sudo()._write_internal(
            {"state": "outcome_unknown", "sii_message": message}
        )
        self.document_id.sudo().action_record_transition(
            "outcome_unknown",
            "sii_submission_outcome_unknown",
            message=message,
            correlation_key=self.idempotency_key,
        )
        return True

    def _record_send_error(self, error, attempt=None):
        """Persist a failure that provably happened before SII received data."""
        self.ensure_one()
        message = str(error)[:4000]
        if attempt:
            attempt._complete(
                "failed",
                error_code=(getattr(error, "code", None) or type(error).__name__)[:128],
                error_summary=message,
            )
        self._write_internal({"state": "error", "sii_message": message})
        return True

    def _validate_document_payload(self, request_bytes):
        """Validate a signed DTE before the transport adapter receives it."""
        self.ensure_one()
        validate_document_payload(
            request_bytes,
            self._get_document_validator,
            allowed_codes={self.document_id.document_code},
        )
        return True

    @api.model
    def _get_document_validator(self, document_code):
        """Extension point for family-specific fiscal validation."""
        return get_document_validator(document_code)

    def _job_query_sii_status(self):
        """Query SII state for a known TrackID and retain the raw response."""
        self.ensure_one()
        if not self.track_id:
            raise UserError(_("A TrackID is required before status can be queried."))
        if self.state not in {"submitted", "processing", "outcome_unknown"}:
            raise UserError(_("This submission is not eligible for status polling."))
        query_number = len(self._query_attempts()) + 1
        attempt = (
            self.env["l10n_cl.submission.attempt"]
            .sudo()
            .create(
                {
                    "submission_id": self.id,
                    "operation": "query",
                    "request_digest": hashlib.sha256(
                        self.track_id.encode("utf-8")
                    ).hexdigest(),
                }
            )
        )
        try:
            result = self._gateway().query_status(
                self.company_id,
                self.environment,
                self.track_id,
                self.demo_scenario,
                query_number,
                self.idempotency_key,
            )
            raw_response = result.get("raw_response")
            state = result.get("state")
            if self.is_simulated and result.get("is_simulated") is not True:
                raise ValidationError(
                    _(
                        "The Demo adapter returned a response without "
                        "its simulation marker."
                    )
                )
            if not isinstance(raw_response, bytes) or not raw_response:
                raise ValidationError(_("The SII status adapter returned no response."))
            if state not in {"submitted", "processing", "accepted", "rejected"}:
                raise ValidationError(
                    _("SII did not return a final status (code %(code)s).")
                    % {"code": result.get("transport_code") or "?"}
                )
            # The status, the submission and the DTE change together or not at all.
            with self.env.cr.savepoint():
                self._apply_status(attempt, result, state, raw_response)
        except Exception as error:
            if isinstance(error, psycopg2.Error):
                raise
            # A failed query never changes what SII already confirmed: the
            # TrackID stays valid and the next poll asks again.
            attempt._complete(
                "failed",
                error_code=type(error).__name__[:128],
                error_summary=str(error)[:4000],
            )
            self.sudo()._write_internal(
                {
                    "last_checked_at": fields.Datetime.now(),
                    "sii_message": str(error)[:4000],
                }
            )
        return True

    def _apply_status(self, attempt, result, state, raw_response):
        submission = self.sudo()
        response = submission._create_response_artifact("status_response", raw_response)
        attempt._complete("completed", response_digest=response.sha256)
        message = self._status_message(result)
        submission._write_internal(
            {
                "state": state,
                "last_checked_at": fields.Datetime.now(),
                "sii_message": message,
                "sii_has_observations": bool(
                    state == "accepted" and result.get("error_summary")
                ),
            }
        )
        submission.document_id.action_record_transition(
            state,
            f"sii_status_{state}",
            message=message or None,
            artifact_id=response.id,
        )

    @staticmethod
    def _status_message(result):
        """Summarize the SII status code, its text and any observations."""
        parts = []
        if result.get("transport_code"):
            parts.append(
                " ".join(
                    part
                    for part in (result["transport_code"], result.get("detail"))
                    if part
                )
            )
        if result.get("error_summary"):
            parts.append(result["error_summary"])
        return "\n".join(parts)[:4000] or False

    def action_enqueue_status_query(self):
        """Queue a status query for an existing TrackID."""
        self.ensure_one()
        if (
            self.state not in {"submitted", "processing", "outcome_unknown"}
            or not self.track_id
        ):
            raise UserError(_("Only a submitted DTE with a TrackID can be queried."))
        self._gateway()
        identity = f"dte-query-{self.id}-{fields.Datetime.now()}"
        self.with_delay(identity_key=identity)._job_query_sii_status()
        return True

    @api.model
    def _cron_poll_sii_status(self, limit=POLL_BATCH_SIZE):
        """Queue status queries for submitted DTEs whose backoff has elapsed."""
        now = fields.Datetime.now()
        # Demo keeps manual queries so its scripted scenarios stay predictable.
        candidates = self.sudo().search(
            [
                ("environment", "=", "production"),
                ("state", "in", ["submitted", "processing", "outcome_unknown"]),
                ("track_id", "!=", False),
                ("submitted_at", ">=", now - POLL_MAX_AGE),
                "|",
                ("last_checked_at", "=", False),
                ("last_checked_at", "<=", now - POLL_BASE_DELAY),
            ],
            order="last_checked_at asc nulls first, id asc",
            limit=limit * POLL_SEARCH_FACTOR,
        )
        queued = 0
        for submission in candidates:
            if queued >= limit:
                break
            if submission._next_poll_at() > now:
                continue
            queries = len(submission._query_attempts())
            submission.with_delay(
                identity_key=f"dte-poll-{submission.id}-{queries}"
            )._job_query_sii_status()
            queued += 1
        return queued

    def _query_attempts(self):
        self.ensure_one()
        return self.attempt_ids.filtered(lambda item: item.operation == "query")

    def _next_poll_at(self):
        """Back off exponentially from the last check, capped at a few hours."""
        self.ensure_one()
        reference = self.last_checked_at or self.submitted_at or self.create_date
        exponent = min(len(self._query_attempts()), POLL_MAX_EXPONENT)
        delay = min(POLL_BASE_DELAY * 2**exponent, POLL_MAX_DELAY)
        return reference + delay

    def action_enqueue_reconciliation(self):
        """Reconcile an ambiguous send without resubmitting the DTE."""
        self.ensure_one()
        if self.state != "outcome_unknown":
            raise UserError(
                _("Only a submission with an unknown outcome can be reconciled.")
            )
        self._gateway()
        identity = f"dte-reconcile-{self.id}-{fields.Datetime.now()}"
        self.with_delay(identity_key=identity)._job_reconcile_sii_submission()
        return True

    def _job_reconcile_sii_submission(self):
        """Look up the prior send result; never issue a second send here."""
        self.ensure_one()
        if self.state != "outcome_unknown":
            return True
        gateway = self._gateway()
        document = self.document_id
        identity = {
            "idempotency_key": self.idempotency_key,
            "document_code": document.document_code,
            "folio": document.folio,
        }
        request_digest = hashlib.sha256(
            repr(sorted(identity.items())).encode("utf-8")
        ).hexdigest()
        attempt = self.env["l10n_cl.submission.attempt"].create(
            {
                "submission_id": self.id,
                "operation": "reconcile",
                "request_digest": request_digest,
            }
        )
        try:
            result = gateway.reconcile_submission(
                self.company_id, self.environment, identity, self.demo_scenario
            )
            raw_response = result.get("raw_response")
            track_id = (result.get("track_id") or "").strip()
            if self.is_simulated and result.get("is_simulated") is not True:
                raise ValidationError(
                    _(
                        "The Demo adapter returned a response without "
                        "its simulation marker."
                    )
                )
            if (
                result.get("state") != "submitted"
                or not track_id
                or not isinstance(raw_response, bytes)
                or not raw_response
            ):
                raise ValidationError(
                    _("The reconciliation adapter returned no confirmed SII receipt.")
                )
            response = self._create_response_artifact("sii_response", raw_response)
            attempt._complete("completed", response_digest=response.sha256)
            self._write_internal(
                {
                    "state": "submitted",
                    "track_id": track_id,
                    "submitted_at": fields.Datetime.now(),
                }
            )
            document.action_record_transition(
                "submitted",
                "sii_submission_reconciled",
                correlation_key=self.idempotency_key,
                artifact_id=response.id,
            )
        except Exception as error:
            unknown = bool(getattr(error, "outcome_unknown", True))
            attempt._complete(
                "unknown" if unknown else "failed",
                error_code=type(error).__name__[:128],
                error_summary=str(error)[:4000],
            )
        return True

    def _create_response_artifact(self, role, content):
        self.ensure_one()
        return self.env["l10n_cl.electronic.artifact"].create(
            {
                "document_id": self.document_id.id,
                "role": role,
                "content": base64.b64encode(content),
                "mimetype": "application/xml",
                "encoding": "UTF-8",
                "producer_version": "l10n_cl_dte_gateway/18.0",
            }
        )
