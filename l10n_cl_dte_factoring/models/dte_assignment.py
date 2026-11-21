# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
import uuid

import psycopg2

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import SQL

from odoo.addons.l10n_cl_dte_gateway.services.certification_auth import (
    SiiTransportError,
)
from odoo.addons.l10n_cl_dte_signing.services.certificate_identity import normalize_rut
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService

from ..services.aec_composer import (
    ASSIGNABLE_DOCUMENT_CODES,
    AecComposer,
    default_sworn_declaration,
)
from ..services.aec_signer import AecSigner
from ..services.aec_validation import (
    check_aec_structure,
    check_embedded_bytes,
    validate_aec_schema,
)
from ..services.assignment_request import (
    build_aec_request,
    certificate_holder_name,
    chile_timestamp,
)
from ..services.rtc_transport import RtcTransport

# States that hold the DTE: only one such assignment may exist per document.
ACTIVE_STATES = (
    "signed",
    "sending",
    "submitted",
    "processing",
    "accepted",
    "outcome_unknown",
    "error",
)
IN_PROGRESS_STATES = frozenset(ACTIVE_STATES) - {"accepted"}
EDITABLE_FIELDS = frozenset(
    {
        "partner_id",
        "amount",
        "last_due_date",
        "sworn_declaration",
        "other_conditions",
        "debtor_email",
        "assignor_email",
        "notification_email",
    }
)
REAL_ENVIRONMENTS = ("certification", "production")


class DteAssignment(models.Model):
    _name = "l10n_cl.dte.assignment"
    _description = "Electronic Invoice Assignment (AEC)"
    _order = "id desc"
    _check_company_auto = True
    _sql_constraints = [
        (
            "assignment_idempotency_uniq",
            "unique(idempotency_key)",
            "The assignment key must be unique.",
        )
    ]

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, index=True
    )
    move_id = fields.Many2one(
        "account.move",
        string="Invoice",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    document_id = fields.Many2one(
        "l10n_cl.electronic.document",
        string="DTE",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    environment = fields.Selection(
        [("certification", "Certification"), ("production", "Production")],
        required=True,
        readonly=True,
    )
    document_code = fields.Char(related="document_id.document_code")
    folio = fields.Integer(related="document_id.folio")
    partner_id = fields.Many2one(
        "res.partner",
        string="Factoring Company",
        required=True,
        check_company=True,
        help="Assignee (cesionario) that receives the receivable.",
    )
    amount = fields.Integer(
        string="Assigned Amount (CLP)",
        required=True,
        help="Amount of the assigned credit; at most the DTE total.",
    )
    last_due_date = fields.Date(required=True)
    sworn_declaration = fields.Text(
        compute="_compute_sworn_declaration",
        store=True,
        readonly=False,
        help="Declaration required when the merchandise receipt is not "
        "electronic (Ley 19.983). Up to 512 characters.",
    )
    other_conditions = fields.Text()
    debtor_email = fields.Char()
    assignor_email = fields.Char(help="Email of the assignor (cedente) in the AEC.")
    notification_email = fields.Char(
        help="Address SII notifies with the upload result."
    )
    sequence = fields.Integer(string="Assignment Sequence", default=1, readonly=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("signed", "AEC Signed"),
            ("sending", "Sending"),
            ("submitted", "Submitted"),
            ("processing", "Processing"),
            ("accepted", "Assigned"),
            ("rejected", "Rejected"),
            ("annulled", "Annulled"),
            ("outcome_unknown", "Outcome Unknown"),
            ("error", "Transport Error"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        readonly=True,
        index=True,
        copy=False,
    )
    idempotency_key = fields.Char(
        required=True, readonly=True, copy=False, default=lambda self: uuid.uuid4().hex
    )
    assignee_vat = fields.Char(readonly=True, copy=False)
    assignee_name = fields.Char(readonly=True, copy=False)
    signer_vat = fields.Char(string="Authorized Signer RUT", readonly=True, copy=False)
    signer_name = fields.Char(string="Authorized Signer", readonly=True, copy=False)
    aec_timestamp = fields.Char(string="AEC Signature Time", readonly=True, copy=False)
    aec_evidence_id = fields.Many2one(
        "l10n_cl.dte.assignment.evidence",
        string="Signed AEC",
        readonly=True,
        copy=False,
        check_company=True,
    )
    aec_sha256 = fields.Char(related="aec_evidence_id.sha256", string="AEC SHA-256")
    track_id = fields.Char(string="TrackID", readonly=True, copy=False, index=True)
    submitted_at = fields.Datetime(readonly=True, copy=False)
    last_checked_at = fields.Datetime(readonly=True, copy=False)
    sii_status = fields.Char(string="SII Status", readonly=True, copy=False)
    sii_message = fields.Text(string="SII Message", readonly=True, copy=False)
    evidence_ids = fields.One2many(
        "l10n_cl.dte.assignment.evidence", "assignment_id", readonly=True
    )
    attempt_ids = fields.One2many(
        "l10n_cl.dte.assignment.attempt", "assignment_id", readonly=True
    )

    def init(self):
        """Allow only one assignment holding each DTE at any time."""
        self.env.cr.execute(
            SQL(
                "CREATE UNIQUE INDEX IF NOT EXISTS %s ON %s (document_id) "
                "WHERE state IN %s",
                SQL.identifier("l10n_cl_dte_assignment_active_document_uniq"),
                SQL.identifier(self._table),
                ACTIVE_STATES,
            )
        )

    @api.depends("document_id.document_code", "document_id.folio")
    def _compute_name(self):
        for assignment in self:
            assignment.name = self.env._(
                "AEC %(code)s-%(folio)s",
                code=assignment.document_id.document_code or "",
                folio=assignment.document_id.folio or "",
            )

    @api.depends("partner_id", "move_id")
    def _compute_sworn_declaration(self):
        for assignment in self:
            company = assignment.company_id.partner_id.commercial_partner_id
            assignee = assignment.partner_id.commercial_partner_id
            debtor = assignment.move_id.commercial_partner_id
            if not (company and assignee and debtor):
                assignment.sworn_declaration = assignment.sworn_declaration
                continue
            assignment.sworn_declaration = default_sworn_declaration(
                *(
                    (partner.name, self._safe_rut(partner.vat))
                    for partner in (company, assignee, debtor)
                )
            )

    @staticmethod
    def _safe_rut(value):
        try:
            return normalize_rut(value)
        except ValidationError:
            return value or ""

    @api.model_create_multi
    def create(self, vals_list):
        """Bind each assignment to its invoice's accepted DTE."""
        self._check_manager()
        prepared = []
        for vals in vals_list:
            values = dict(vals)
            move = self.env["account.move"].browse(values.get("move_id")).exists()
            if not move:
                raise ValidationError(self.env._("An invoice is required."))
            document = move._get_dte_assignable_document()
            values.update(
                {
                    "company_id": move.company_id.id,
                    "document_id": document.id,
                    "environment": document.sii_environment,
                    "state": "draft",
                }
            )
            for key, value in self._default_values(move).items():
                values.setdefault(key, value)
            prepared.append(values)
        return super().create(prepared)

    @api.model
    def _default_values(self, move):
        return {
            "amount": int(round(abs(move.amount_total_signed))),
            "last_due_date": move.invoice_date_due or move.invoice_date,
            "assignor_email": move.company_id.email,
            "notification_email": self.env.user.email or move.company_id.email,
            "debtor_email": move.commercial_partner_id.email,
        }

    def write(self, vals):
        """Users edit draft terms only; workflow fields change internally."""
        if set(vals) - EDITABLE_FIELDS:
            raise UserError(
                self.env._("Assignment state changes only through its actions.")
            )
        self._check_manager()
        if any(assignment.state != "draft" for assignment in self):
            raise UserError(self.env._("Only a draft assignment can be edited."))
        return super().write(vals)

    def _write_internal(self, values):
        return super().write(values)

    def unlink(self):
        if any(assignment.state != "draft" for assignment in self):
            raise UserError(
                self.env._("Signed assignments are retained as audit evidence.")
            )
        self._check_manager()
        return super().unlink()

    def _check_manager(self):
        if not self.env.su and not self.env.user.has_group(
            "l10n_cl_dte_core.group_dte_manager"
        ):
            raise AccessError(self.env._("Only a DTE manager can assign invoices."))

    # -- AEC -------------------------------------------------------------

    def action_build_aec(self):
        """Compose, sign and validate the AEC, then keep it as evidence."""
        self.ensure_one()
        self._check_manager()
        self.check_access("write")
        self._lock()
        if self.state != "draft":
            raise UserError(self.env._("The AEC of this assignment is already signed."))
        self.move_id._get_dte_assignable_document(ignore=self)
        dte_bytes = self._signed_dte_bytes()
        service = SignatureService()
        profile, holder = service.signing_identity(
            self.company_id, self.environment, manager_only=True
        )
        certificate = profile.sudo().certificate_id
        signer_name = certificate_holder_name(certificate)
        timestamp = self._aec_timestamp()
        request = build_aec_request(self, dte_bytes, holder, signer_name, timestamp)
        aec_bytes = AecSigner(certificate, service).sign(AecComposer().compose(request))
        validate_aec_schema(aec_bytes)
        check_aec_structure(aec_bytes, dte_bytes)
        check_embedded_bytes(aec_bytes, dte_bytes)
        evidence = self._create_evidence(
            "aec_xml", aec_bytes, f"AEC_{self.document_code}_{self.folio}.xml"
        )
        self._write_internal(
            {
                "state": "signed",
                "aec_evidence_id": evidence.id,
                "assignee_vat": request.assignee.vat,
                "assignee_name": request.assignee.name,
                "signer_vat": request.signer_vat,
                "signer_name": signer_name,
                "aec_timestamp": timestamp,
                "sii_message": False,
            }
        )
        return True

    def _aec_timestamp(self):
        """Signature time in Chile; tests patch this for reproducible files."""
        return chile_timestamp()

    def _signed_dte_bytes(self):
        artifact = self.document_id.sudo()._get_signed_artifact()
        artifact.action_verify_integrity()
        return base64.b64decode(
            artifact.with_context(bin_size=False).content, validate=True
        )

    def _create_evidence(self, role, content, file_name=False):
        return (
            self.env["l10n_cl.dte.assignment.evidence"]
            .sudo()
            .create(
                {
                    "assignment_id": self.id,
                    "role": role,
                    "content": base64.b64encode(content),
                    "file_name": file_name,
                }
            )
        )

    def _lock(self):
        self.flush_recordset()
        self.env.cr.execute(
            SQL(
                "SELECT id FROM %s WHERE id = %s FOR UPDATE",
                SQL.identifier(self._table),
                self.id,
            )
        )
        self.invalidate_recordset()

    # -- RTC upload ------------------------------------------------------

    def _rtc_transport(self):
        """Return the RTC client of this assignment's immutable SII mode."""
        self.ensure_one()
        if self.environment not in REAL_ENVIRONMENTS or (
            self.company_id.dte_sii_environment != self.environment
        ):
            raise UserError(
                self.env._("The company SII mode differs from this assignment's.")
            )
        return RtcTransport(self.environment)

    def _dispatched_send_attempts(self):
        return self.attempt_ids.filtered(
            lambda attempt: attempt.operation == "send" and attempt.state != "failed"
        )

    def action_send(self):
        """Queue the RTC upload once; repeated clicks never send twice."""
        self.ensure_one()
        self._check_manager()
        if self.state == "error" and not self._dispatched_send_attempts():
            # Every earlier attempt provably failed before reaching SII.
            self._write_internal({"state": "signed"})
        if self.state in {"sending", "submitted", "processing", "accepted"}:
            return True
        if self.state != "signed":
            raise UserError(self.env._("Only a signed AEC can be sent to SII."))
        self.aec_evidence_id._raw_content()
        self._rtc_transport()
        self.with_delay(identity_key=f"aec-send-{self.idempotency_key}")._job_send_aec()
        return True

    def _job_send_aec(self):
        """Upload once and keep the receipt; a lost answer stays unknown."""
        self.ensure_one()
        self._lock()
        if self.state != "signed":
            return True
        try:
            aec_bytes = self.aec_evidence_id._raw_content()
            transport = self._rtc_transport()
        except (UserError, ValidationError) as error:
            return self._record_send_error(error)
        self._write_internal({"state": "sending", "sii_message": False})
        attempt = (
            self.env["l10n_cl.dte.assignment.attempt"]
            .sudo()
            .create(
                {
                    "assignment_id": self.id,
                    "operation": "send",
                    "request_digest": hashlib.sha256(aec_bytes).hexdigest(),
                }
            )
        )
        try:
            result = transport.upload(
                self.company_id,
                aec_bytes,
                self.company_id.partner_id.vat,
                self.notification_email or self.assignor_email,
            )
        except SiiTransportError as error:
            if not error.dispatched:
                return self._record_send_error(error, attempt)
            return self._record_unknown(attempt, error)
        except (UserError, ValidationError) as error:
            # Configuration and token failures happen before the upload.
            return self._record_send_error(error, attempt)
        except Exception as error:  # the upload may have reached SII
            if isinstance(error, psycopg2.Error):
                raise
            return self._record_unknown(attempt, error)
        try:
            return self._apply_upload_result(attempt, result)
        except Exception as error:  # the upload already happened
            if isinstance(error, psycopg2.Error):
                raise
            return self._record_unknown(attempt, error)

    def _apply_upload_result(self, attempt, result):
        evidence = self._create_evidence("upload_response", result["raw_response"])
        values = {"sii_status": result["code"], "sii_message": result["message"]}
        if result["state"] == "submitted":
            attempt._complete("completed", response_digest=evidence.sha256)
            values.update(
                {
                    "state": "submitted",
                    "track_id": result["track_id"],
                    "submitted_at": fields.Datetime.now(),
                }
            )
        elif result["state"] == "not_received":
            attempt._complete(
                "failed",
                response_digest=evidence.sha256,
                error_code=f"upload_status_{result['code']}",
                summary=result["message"],
            )
            values["state"] = "error"
        else:
            attempt._complete("completed", response_digest=evidence.sha256)
            values["state"] = "rejected"
        self._write_internal(values)
        return True

    def _record_send_error(self, error, attempt=None):
        """Persist a failure that provably happened before SII got the AEC."""
        message = str(error)[:4000]
        if attempt:
            attempt._complete(
                "failed",
                error_code=getattr(error, "code", None) or type(error).__name__,
                summary=message,
            )
        self._write_internal({"state": "error", "sii_message": message})
        return True

    def _record_unknown(self, attempt, error):
        """The AEC may have reached SII: never upload it again automatically."""
        message = str(error)[:4000]
        response = getattr(error, "response", None)
        digest = False
        if isinstance(response, bytes) and response:
            digest = self._create_evidence("upload_response", response).sha256
        attempt._complete(
            "unknown",
            response_digest=digest,
            error_code=getattr(error, "code", None) or type(error).__name__,
            summary=message,
        )
        self._write_internal(
            {
                "state": "outcome_unknown",
                "sii_message": self.env._(
                    "The SII answer to the upload was lost (%(error)s). Check the "
                    "assignment registry before any new upload.",
                    error=message,
                ),
            }
        )
        return True

    # -- RTC status ------------------------------------------------------

    def action_query_status(self):
        """Query the upload state of the TrackID and apply the SII answer."""
        self.ensure_one()
        self._check_manager()
        if self.state not in {"submitted", "processing"} or not self.track_id:
            raise UserError(
                self.env._("Only a submitted AEC with a TrackID can be queried.")
            )
        return self._run_query(
            "query",
            self.track_id.encode("ascii"),
            lambda transport: transport.query_upload(self.company_id, self.track_id),
            self._apply_status_result,
        )

    def action_reconcile(self):
        """Ask the SII registry whether the DTE is assigned; never re-upload."""
        self.ensure_one()
        self._check_manager()
        if self.state != "outcome_unknown":
            raise UserError(
                self.env._("Only an assignment with an unknown outcome is reconciled.")
            )
        identity = f"{self.document_code}:{self.folio}".encode("ascii")
        return self._run_query(
            "reconcile",
            identity,
            lambda transport: transport.query_assignment(
                self.company_id,
                self.company_id.partner_id.vat,
                self.document_code,
                self.folio,
            ),
            self._apply_reconcile_result,
        )

    def _run_query(self, operation, request_identity, call, apply):
        attempt = (
            self.env["l10n_cl.dte.assignment.attempt"]
            .sudo()
            .create(
                {
                    "assignment_id": self.id,
                    "operation": operation,
                    "request_digest": hashlib.sha256(request_identity).hexdigest(),
                }
            )
        )
        try:
            result = call(self._rtc_transport())
        except (SiiTransportError, UserError, ValidationError) as error:
            # A read-only query changes nothing at SII; keep the current state.
            attempt._complete(
                "failed",
                error_code=getattr(error, "code", None) or type(error).__name__,
                summary=str(error),
            )
            self._write_internal(
                {
                    "sii_message": str(error)[:4000],
                    "last_checked_at": fields.Datetime.now(),
                }
            )
            return True
        role = "status_response" if operation == "query" else "reconcile_response"
        evidence = self._create_evidence(role, result["raw_response"])
        attempt._complete("completed", response_digest=evidence.sha256)
        apply(result)
        return True

    def _apply_status_result(self, result):
        values = {
            "sii_status": result["code"],
            "sii_message": result["message"],
            "last_checked_at": fields.Datetime.now(),
        }
        if result["state"] in {"processing", "accepted", "rejected", "annulled"}:
            values["state"] = result["state"]
        self._write_internal(values)

    def _apply_reconcile_result(self, result):
        values = {
            "sii_status": result["code"],
            "last_checked_at": fields.Datetime.now(),
        }
        if result["assigned"]:
            values["state"] = "accepted"
            values["sii_message"] = self.env._(
                "SII records this DTE as assigned (%(message)s). Confirm the "
                "assignee in the SII assignment registry.",
                message=result["message"],
            )
        else:
            values["sii_message"] = self.env._(
                "SII does not record this DTE as assigned yet (%(message)s). Do "
                "not upload again before SII finishes processing.",
                message=result["message"],
            )
        self._write_internal(values)

    def action_cancel(self):
        """Release the DTE; an unknown outcome needs a human decision first."""
        self.ensure_one()
        self._check_manager()
        if self.state not in {"draft", "signed", "error", "outcome_unknown"}:
            raise UserError(
                self.env._("A submitted or assigned AEC cannot be cancelled here.")
            )
        if self.state == "error" and self._dispatched_send_attempts():
            raise UserError(
                self.env._("Reconcile this assignment with SII before cancelling.")
            )
        self._write_internal({"state": "cancelled"})
        return True

    @api.model
    def _assignable_codes(self):
        return ASSIGNABLE_DOCUMENT_CODES
