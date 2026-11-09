# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
from collections import Counter

import psycopg2
from cryptography import x509
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.l10n_cl_dte_signing.services.certificate_identity import (
    certificate_sender_rut,
    normalize_rut,
)
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService

from ..services.certification_auth import SiiTransportError
from ..services.certification_response_parser import (
    parse_status,
    parse_upload,
    parse_xml,
    soap_payload,
    text,
)


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
            ("accepted_with_observations", "Accepted with Observations"),
            ("partially_accepted", "Partially Accepted"),
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
    transport_code = fields.Char(readonly=True, copy=False)
    result_counts = fields.Json(readonly=True, copy=False)
    attempt_ids = fields.One2many("l10n_cl.sii.envelope.attempt", "submission_id")
    response_code = fields.Char(
        string="Last SII Response Code",
        compute="_compute_response_summary",
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    response_detail = fields.Text(
        string="Original SII Message",
        compute="_compute_response_summary",
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    response_notice = fields.Text(
        string="SII Response Summary",
        compute="_compute_response_summary",
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    response_warning = fields.Text(
        string="Response Verification",
        compute="_compute_response_summary",
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    response_received_at = fields.Datetime(
        compute="_compute_response_summary",
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    reported_rejection = fields.Boolean(
        compute="_compute_response_summary",
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    response_document_summary = fields.Html(
        string="Document Results",
        compute="_compute_response_summary",
        groups="l10n_cl_dte_core.group_dte_manager",
    )

    @api.depends(
        "transport_code",
        "attempt_ids.response_xml",
        "attempt_ids.response_digest",
        "attempt_ids.error_summary",
        "attempt_ids.finished_at",
        "attempt_ids.operation",
        "track_id",
        "request_xml",
        "request_digest",
    )
    def _compute_response_summary(self):
        for submission in self:
            submission.response_code = False
            submission.response_detail = False
            submission.response_notice = False
            submission.response_warning = False
            submission.response_received_at = False
            submission.reported_rejection = False
            submission.response_document_summary = False
            attempt = submission.attempt_ids[:1].with_context(bin_size=False)
            if not attempt:
                continue
            submission.response_received_at = attempt.finished_at
            warning = attempt.error_summary
            identity_message = (
                "The SII upload receipt belongs to a different issuer or sender."
            )
            if warning in (
                identity_message,
                _("The SII upload receipt belongs to a different issuer or sender."),
            ):
                warning = _(
                    "The issuer or sender in the receipt differs from the envelope "
                    "configuration. The transmission result remains unconfirmed."
                )
            submission.response_warning = warning
            if not attempt.response_xml:
                continue
            try:
                content = base64.b64decode(attempt.response_xml, validate=True)
                if hashlib.sha256(content).hexdigest() != attempt.response_digest:
                    raise ValidationError(
                        _("The stored SII response fingerprint changed.")
                    )
                response = parse_xml(content)
                if response.tag == "RECEPCIONDTE":
                    code = text(response, "STATUS")
                    detail = "\n".join(response.xpath("./DETAIL/ERROR/text()")[:10])[
                        :4000
                    ]
                    rejected = code.isdigit() and int(code) != 0
                    submission.reported_rejection = rejected
                    if rejected:
                        reason = _("Review the original SII message.")
                        if "CHR-00001" in detail:
                            reason = _("The file character encoding was rejected.")
                        submission.response_notice = _(
                            "SII reported a reception rejection (code %(code)s). "
                            "%(reason)s"
                        ) % {"code": code, "reason": reason}
                        if not text(response, "TRACKID", required=False):
                            submission.response_notice += " " + _(
                                "No TrackID was assigned."
                            )
                elif attempt.operation == "status":
                    response = soap_payload(content, "getEstUp")
                    code = text(response, "ESTADO")
                    detail = text(response, "GLOSA", required=False)
                    if code in {"RCT", "RSC", "RFR"}:
                        submission.reported_rejection = True
                        submission.response_notice = _(
                            "SII rejected the envelope (%(code)s): %(reason)s"
                        ) % {"code": code, "reason": detail}
                    elif code == "EPR":
                        counts = parse_status(content, submission.track_id)["counts"]
                        if counts:
                            submission._check_document_counts(counts)
                            submission._set_document_result_summary(counts)
                else:
                    raise ValidationError(
                        _("The stored SII response format is not recognized.")
                    )
                submission.response_code = code
                submission.response_detail = detail
            except (TypeError, ValueError, ValidationError):
                submission.response_warning = _(
                    "The stored SII response could not be verified. "
                    "Review the transmission evidence."
                )

    def _check_document_counts(self, counts):
        self.ensure_one()
        request = base64.b64decode(self.with_context(bin_size=False).request_xml)
        if hashlib.sha256(request).hexdigest() != self.request_digest:
            raise ValidationError(_("The envelope digest does not match its bytes."))
        expected = Counter(
            parse_xml(request).xpath(
                ".//*[local-name()='DTE']/*[local-name()='Documento']"
                "/*[local-name()='Encabezado']/*[local-name()='IdDoc']"
                "/*[local-name()='TipoDTE']/text()"
            )
        )
        actual = Counter()
        for row in counts:
            actual[str(row["TIPO_DOCTO"])] += row["INFORMADOS"]
        if not expected or actual != expected:
            raise ValidationError(
                _("The SII response does not cover every DTE in this envelope.")
            )

    def _set_document_result_summary(self, counts):
        self.ensure_one()
        columns = (
            ("TIPO_DOCTO", _("Document Type")),
            ("INFORMADOS", _("Informed Documents")),
            ("ACEPTADOS", _("Accepted Documents")),
            ("RECHAZADOS", _("Rejected Documents")),
            ("REPAROS", _("Documents with Observations")),
        )
        headings = Markup("").join(
            Markup("<th>%s</th>") % label for _key, label in columns
        )
        rows = Markup("").join(
            Markup("<tr>%s</tr>")
            % Markup("").join(
                Markup("<td>%s</td>") % row[key] for key, _label in columns
            )
            for row in counts
        )
        self.response_document_summary = Markup(
            '<table class="table table-sm table-bordered">'
            "<thead><tr>%s</tr></thead><tbody>%s</tbody></table>"
        ) % (headings, rows)
        totals = {
            key: sum(row[key] for row in counts)
            for key in ("INFORMADOS", "ACEPTADOS", "RECHAZADOS", "REPAROS")
        }
        self.reported_rejection = bool(totals["RECHAZADOS"])
        self.response_notice = _(
            "SII processed %(total)s DTE: %(accepted)s accepted, "
            "%(rejected)s rejected and %(observed)s accepted with observations."
        ) % {
            "total": totals["INFORMADOS"],
            "accepted": totals["ACEPTADOS"],
            "rejected": totals["RECHAZADOS"],
            "observed": totals["REPAROS"],
        }
        if self.reported_rejection:
            self.response_notice += " " + _(
                "The status response does not include the individual rejection reasons."
            )

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

    def action_refresh_response(self):
        """Reopen the same dialog from stored evidence; never contact SII."""
        self.ensure_one()
        self._check_manager()
        self.invalidate_recordset()
        action = self.action_open()
        action["target"] = "new"
        return action

    def action_enqueue_send(self):
        self.ensure_one()
        self._check_manager()
        if self.state == "error" and self.attempt_ids[:1].state == "failed":
            self._write_internal({"state": "pending"})
        if self.state != "pending":
            raise UserError(_("Only a pending envelope can be sent."))
        self._verify_request()
        gateway = self.env["l10n_cl.sii.gateway.factory"].get_envelope_gateway(
            self.environment
        )
        gateway.validate_request(
            self.company_id,
            self.environment,
            base64.b64decode(self.with_context(bin_size=False).request_xml),
        )
        self.with_delay(
            identity_key=f"sii-envelope-{self.idempotency_key}-{len(self.attempt_ids)}"
        )._job_send()
        return self.action_open()

    def _check_manager(self):
        self.check_access("read")
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(_("Only a DTE manager can submit an SII envelope."))
        if self.company_id.dte_sii_environment != self.environment:
            raise UserError(_("The envelope mode differs from the company mode."))

    def _claim_dispatch(self):
        """Commit a one-use claim before external I/O, independently of the job.

        A separate cursor transaction is necessary here: losing the job's main
        transaction after SII receives a request must never make it sendable again.
        The cursor context owns commit/rollback; it contains no external calls.
        """
        self.ensure_one()
        with self.env.registry.cursor() as claim_cr:
            claim_env = api.Environment(claim_cr, self.env.uid, dict(self.env.context))
            claim_cr.execute(
                "SELECT state FROM l10n_cl_sii_envelope_submission "
                "WHERE id=%s FOR UPDATE",
                [self.id],
            )
            row = claim_cr.fetchone()
            if not row or row[0] != "pending":
                return False
            claimed = self.with_env(claim_env)
            claimed._check_manager()
            claimed._verify_request()
            attempt = claim_env["l10n_cl.sii.envelope.attempt"]._create_for_operation(
                claimed, "send"
            )
            claimed._write_internal({"state": "sending"})
            attempt_id = attempt.id
        self.invalidate_recordset()
        return attempt_id

    def _job_send(self):
        self.ensure_one()
        self._check_manager()
        self.invalidate_recordset()
        if self.state != "pending":
            return True
        self._verify_request()
        attempt_id = self._claim_dispatch()
        if not attempt_id:
            return True
        self._dispatch_claimed(attempt_id)
        return True

    def _dispatch_claimed(self, attempt_id):
        """Use a fresh snapshot after the claim's independent transaction.

        Odoo jobs use repeatable-read isolation. Their original cursor cannot
        observe a claim committed after its snapshot, nor safely update that row.
        Keep response recording in this second independent transaction instead.
        """
        with self.env.registry.cursor() as dispatch_cr:
            dispatch_env = api.Environment(
                dispatch_cr, self.env.uid, dict(self.env.context)
            )
            self.with_env(dispatch_env)._send_claimed(attempt_id)

    def _send_claimed(self, attempt_id):
        self.ensure_one()
        self._check_manager()
        if self.state != "sending":
            return True
        self._verify_request()
        gateway = self.env["l10n_cl.sii.gateway.factory"].get_envelope_gateway(
            self.environment
        )
        attempt = self.env["l10n_cl.sii.envelope.attempt"].browse(attempt_id)
        if attempt.submission_id != self or attempt.state != "running":
            raise UserError(_("The SII dispatch claim is not valid for this envelope."))
        response = None
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
            state = result.get("state", "submitted")
            if (
                not isinstance(response, bytes)
                or not response
                or state not in {"submitted", "rejected"}
                or (state == "submitted" and not track_id)
            ):
                raise ValidationError(
                    _("SII did not return a TrackID and response evidence.")
                )
            attempt._complete("completed", response)
            self._write_internal(
                {
                    "state": state,
                    "track_id": track_id,
                    "transport_code": result.get("transport_code"),
                    "submitted_at": fields.Datetime.now(),
                }
            )
        except psycopg2.Error:
            raise
        except SiiTransportError as error:
            attempt._complete(
                "unknown" if error.dispatched else "failed",
                response=error.response,
                error_summary=str(error),
            )
            self._write_internal(
                {"state": "outcome_unknown" if error.dispatched else "error"}
            )
        except Exception as error:
            attempt._complete(
                "unknown",
                response=response,
                error_summary=_("The SII outcome could not be verified (%(error)s).")
                % {"error": type(error).__name__},
            )
            self._write_internal({"state": "outcome_unknown"})
        return True

    def action_enqueue_status_query(self):
        self.ensure_one()
        self._check_manager()
        if (
            self.state not in {"submitted", "processing", "outcome_unknown"}
            or not self.track_id
        ):
            raise UserError(_("A TrackID is required to query SII status."))
        self.with_delay(
            identity_key=f"sii-envelope-query-{self.id}-{fields.Datetime.now()}"
        )._job_query()
        return self.action_refresh_response()

    def _job_query(self):
        self.ensure_one()
        self._check_manager()
        self.env.cr.execute(
            "SELECT id FROM l10n_cl_sii_envelope_submission WHERE id=%s FOR UPDATE",
            [self.id],
        )
        self.invalidate_recordset()
        if (
            self.state not in {"submitted", "processing", "outcome_unknown"}
            or not self.track_id
        ):
            return True
        gateway = self.env["l10n_cl.sii.gateway.factory"].get_envelope_gateway(
            self.environment
        )
        attempt = self.env["l10n_cl.sii.envelope.attempt"]._create_for_operation(
            self, "status"
        )
        response = None
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
                "accepted_with_observations",
                "partially_accepted",
                "outcome_unknown",
            }:
                raise ValidationError(_("The SII status response is invalid."))
            if not isinstance(response, bytes) or not response:
                raise ValidationError(_("The SII status response has no evidence."))
            if result.get("track_id") != self.track_id:
                raise ValidationError(
                    _("The SII status response belongs to a different TrackID.")
                )
            counts = result.get("counts", [])
            if state in {
                "accepted",
                "accepted_with_observations",
                "partially_accepted",
            } or (state == "rejected" and counts):
                self._check_document_counts(counts)
            attempt._complete("completed", response)
            self._write_internal(
                {
                    "state": state,
                    "last_checked_at": fields.Datetime.now(),
                    "transport_code": result.get("transport_code"),
                    "result_counts": counts,
                }
            )
        except psycopg2.Error:
            raise
        except Exception as error:
            attempt._complete(
                "unknown",
                response=response or getattr(error, "response", None),
                error_summary=_("The SII status could not be verified (%(error)s).")
                % {"error": type(error).__name__},
            )
            self._write_internal({"state": "outcome_unknown"})
        return True

    def _verified_schema_rejection(self):
        """Prove a complete rejection before revising the same envelope."""
        return self._verified_retained_outcome()

    def _verified_terminal_outcome(self):
        """Prove a final result before a separate execution uses new folios."""
        return self._verified_retained_outcome(allow_processed=True)

    def _verified_retained_outcome(self, *, allow_processed=False):
        """Verify the request, upload receipt and retained status without I/O.

        Historical configurations may have used the company RUT as sender.
        The receipt's sender must instead match the holder of the certificate
        embedded in the original, cryptographically verified envelope.
        """
        self.ensure_one()
        self._check_manager()
        allowed_states = {"outcome_unknown", "rejected"}
        if allow_processed:
            allowed_states |= {
                "accepted",
                "accepted_with_observations",
                "partially_accepted",
            }
        if self.state not in allowed_states:
            raise UserError(_("Only a verified envelope rejection can be corrected."))
        self._verify_request()
        request = base64.b64decode(self.with_context(bin_size=False).request_xml)
        SignatureService().verify_envelope_signature(request)
        envelope = parse_xml(request)
        if envelope.tag != "{http://www.sii.cl/SiiDte}EnvioDTE":
            raise ValidationError(
                _("Only a signed EnvioDTE envelope can be transmitted.")
            )
        issuer = text(envelope, "RutEmisor")
        if normalize_rut(issuer) != normalize_rut(self.company_id.vat):
            raise ValidationError(_("The envelope issuer differs from the company."))
        certificates = envelope.xpath(
            "./ds:Signature/ds:KeyInfo/ds:X509Data/ds:X509Certificate/text()",
            namespaces={"ds": "http://www.w3.org/2000/09/xmldsig#"},
        )
        if len(certificates) != 1:
            raise ValidationError(
                _("The original envelope signing identity is ambiguous.")
            )
        certificate = x509.load_der_x509_certificate(
            base64.b64decode("".join(certificates[0].split()), validate=True)
        )
        sender = certificate_sender_rut(certificate)
        attempt = self.attempt_ids[:1].with_context(bin_size=False)
        if (
            not attempt
            or attempt.operation not in {"send", "status"}
            or attempt.state not in {"completed", "unknown"}
            or not attempt.finished_at
            or attempt.request_digest != self.request_digest
            or not attempt.response_xml
        ):
            raise UserError(_("A complete original SII rejection receipt is required."))
        response = base64.b64decode(attempt.response_xml, validate=True)
        if hashlib.sha256(response).hexdigest() != attempt.response_digest:
            raise ValidationError(_("The stored SII response fingerprint changed."))
        if attempt.operation == "status":
            return self._verified_cover_rejection(
                response, issuer, sender, allow_processed=allow_processed
            )
        receipt = parse_xml(response)
        if (
            self.track_id
            or text(receipt, "STATUS") != "7"
            or text(receipt, "TRACKID", required=False)
        ):
            raise UserError(_("Only a verified envelope rejection can be corrected."))
        result = parse_upload(response, issuer, sender)
        if result["state"] != "rejected":
            raise UserError(_("Only a verified envelope rejection can be corrected."))
        return {
            "submission_id": self.id,
            "response_digest": attempt.response_digest,
            "code": "7",
            "sender": sender,
        }

    def _verified_cover_rejection(
        self, response, issuer, sender, *, allow_processed=False
    ):
        """Link a final status to the original successful upload receipt."""
        payload = soap_payload(response, "getEstUp")
        code = text(payload, "ESTADO")
        result = parse_status(response, self.track_id)
        allowed_states = {"rejected"}
        allowed_codes = {"RCT", "EPR"}
        if allow_processed:
            allowed_states |= {
                "accepted",
                "accepted_with_observations",
                "partially_accepted",
            }
            allowed_codes |= {"RFR", "RSC"}
        if (
            self.state not in allowed_states
            or not self.track_id
            or text(payload, "TRACKID") != self.track_id
            or code not in allowed_codes
            or result["state"] != self.state
        ):
            raise UserError(_("Only a verified envelope rejection can be corrected."))
        if code != "EPR":
            if payload.xpath(
                ".//*[local-name()='ACEPTADOS' or local-name()='REPAROS']"
            ):
                raise UserError(
                    _("Only a verified envelope rejection can be corrected.")
                )
        else:
            self._check_document_counts(result["counts"])
        sends = self.attempt_ids.filtered(lambda a: a.operation == "send")
        if len(sends) != 1 or sends.state != "completed" or not sends.finished_at:
            raise UserError(_("A complete original SII rejection receipt is required."))
        original = base64.b64decode(
            sends.with_context(bin_size=False).response_xml, validate=True
        )
        if (
            sends.request_digest != self.request_digest
            or hashlib.sha256(original).hexdigest() != sends.response_digest
        ):
            raise ValidationError(_("The stored SII response fingerprint changed."))
        upload = parse_upload(original, issuer, sender)
        if upload["state"] != "submitted" or upload["track_id"] != self.track_id:
            raise UserError(_("A complete original SII rejection receipt is required."))
        return {
            "submission_id": self.id,
            "response_digest": self.attempt_ids[:1].response_digest,
            "upload_response_digest": sends.response_digest,
            "track_id": self.track_id,
            "code": code,
            "counts": result["counts"],
            "sender": sender,
        }

    def _verify_request(self):
        self.ensure_one()
        content = base64.b64decode(
            self.with_context(bin_size=False).request_xml, validate=True
        )
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
        attempt = super().create(
            {
                "submission_id": submission.id,
                "operation": operation,
                "started_at": fields.Datetime.now(),
                "request_digest": submission.request_digest,
            }
        )
        # Inverse cache updates append records without reapplying _order.
        # Consumers need the latest attempt even within the same transaction.
        submission.invalidate_recordset(["attempt_ids"])
        return attempt

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
