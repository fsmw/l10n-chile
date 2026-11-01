import base64
import hashlib

from odoo import _, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import SQL

from odoo.addons.l10n_cl_dte_xml.services.xml_composer import DteXmlComposer

from ..services.sii_gateway import SiiGateway


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
        [("certification", "Certification"), ("production", "Production")],
        required=True,
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
    attempt_ids = fields.One2many("l10n_cl.submission.attempt", "submission_id")

    def write(self, vals):  # pylint: disable=method-required-super
        raise UserError(_("Submission state can only change through workflow actions."))

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("Submission records are retained as audit evidence."))

    def _write_internal(self, values):
        return super().write(values)

    def action_enqueue_submission(self):
        """Schedule one submission using a durable OCA queue job."""
        self.ensure_one()
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
        document = self.document_id
        signed = document.artifact_ids.filtered(lambda item: item.role == "signed_xml")
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
        document = self.document_id
        signed = document.artifact_ids.filtered(lambda item: item.role == "signed_xml")
        if len(signed) != 1:
            raise ValidationError(
                _("A submission needs exactly one signed DTE artifact.")
            )
        signed.action_verify_integrity()
        request_bytes = base64.b64decode(signed.content, validate=True)
        DteXmlComposer().validate_document(request_bytes)
        self._write_internal({"state": "sending"})
        attempt = self.env["l10n_cl.submission.attempt"].create(
            {
                "submission_id": self.id,
                "operation": "send",
                "request_digest": hashlib.sha256(request_bytes).hexdigest(),
            }
        )
        try:
            result = SiiGateway().send_document(
                self.company_id, self.environment, request_bytes
            )
            raw_response = result.get("raw_response")
            track_id = (result.get("track_id") or "").strip()
            if not isinstance(raw_response, bytes) or not raw_response:
                raise ValidationError(_("The SII adapter returned no raw response."))
            if result.get("state") != "submitted" or not track_id:
                raise ValidationError(_("The SII adapter returned an invalid receipt."))
            response = self._create_response_artifact("sii_response", raw_response)
            attempt._complete(
                "completed",
                response_digest=response.sha256,
            )
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

    def _job_query_sii_status(self):
        """Query SII state for a known TrackID and retain the raw response."""
        self.ensure_one()
        if not self.track_id:
            raise UserError(_("A TrackID is required before status can be queried."))
        if self.state not in {"submitted", "processing", "outcome_unknown"}:
            raise UserError(_("This submission is not eligible for status polling."))
        attempt = self.env["l10n_cl.submission.attempt"].create(
            {
                "submission_id": self.id,
                "operation": "query",
                "request_digest": hashlib.sha256(
                    self.track_id.encode("utf-8")
                ).hexdigest(),
            }
        )
        try:
            result = SiiGateway().query_status(
                self.company_id, self.environment, self.track_id
            )
            raw_response = result.get("raw_response")
            state = result.get("state")
            if not isinstance(raw_response, bytes) or not raw_response:
                raise ValidationError(_("The SII status adapter returned no response."))
            if state not in {"submitted", "processing", "accepted", "rejected"}:
                raise ValidationError(
                    _("The SII status adapter returned an unknown state.")
                )
            response = self._create_response_artifact("status_response", raw_response)
            attempt._complete("completed", response_digest=response.sha256)
            self._write_internal(
                {"state": state, "last_checked_at": fields.Datetime.now()}
            )
            self.document_id.action_record_transition(
                state, f"sii_status_{state}", artifact_id=response.id
            )
        except Exception as error:
            unknown = bool(getattr(error, "outcome_unknown", True))
            attempt._complete(
                "unknown" if unknown else "failed",
                error_code=type(error).__name__[:128],
                error_summary=str(error)[:4000],
            )
            if unknown:
                self._write_internal({"state": "outcome_unknown"})
                self.document_id.action_record_transition(
                    "outcome_unknown",
                    "sii_status_outcome_unknown",
                    correlation_key=self.idempotency_key,
                )
        return True

    def action_enqueue_status_query(self):
        """Queue a status query for an existing TrackID."""
        self.ensure_one()
        if (
            self.state not in {"submitted", "processing", "outcome_unknown"}
            or not self.track_id
        ):
            raise UserError(_("Only a submitted DTE with a TrackID can be queried."))
        identity = f"dte-query-{self.id}-{fields.Datetime.now()}"
        self.with_delay(identity_key=identity)._job_query_sii_status()
        return True

    def action_enqueue_reconciliation(self):
        """Reconcile an ambiguous send without resubmitting the DTE."""
        self.ensure_one()
        if self.state != "outcome_unknown":
            raise UserError(
                _("Only a submission with an unknown outcome can be reconciled.")
            )
        identity = f"dte-reconcile-{self.id}-{fields.Datetime.now()}"
        self.with_delay(identity_key=identity)._job_reconcile_sii_submission()
        return True

    def _job_reconcile_sii_submission(self):
        """Look up the prior send result; never issue a second send here."""
        self.ensure_one()
        if self.state != "outcome_unknown":
            return True
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
            result = SiiGateway().reconcile_submission(
                self.company_id, self.environment, identity
            )
            raw_response = result.get("raw_response")
            track_id = (result.get("track_id") or "").strip()
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
