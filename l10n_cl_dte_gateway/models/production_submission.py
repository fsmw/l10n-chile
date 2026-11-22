# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta

from lxml import etree

from odoo import _, api, fields, models, modules
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.l10n_cl_dte_signing.services.certificate_identity import normalize_rut
from odoo.addons.l10n_cl_dte_signing.services.signature_service import (
    _without_xml_declaration,
)

from ..services.certification_auth import SiiTransportError
from ..services.document_validation import validate_document_payload
from ..services.production_document_outcome import ProductionDocumentOutcome
from ..services.production_gateway import (
    SII_ENVELOPE_ENVIRONMENTS,
    ProductionSiiGateway,
    environment_label,
)
from ..services.production_response_parser import parse_upload, parse_xml

SII = "{http://www.sii.cl/SiiDte}"
BINDING = "transport_request_artifact_id"
# Far beyond one upload with its token retries (60 s read timeouts): a send
# claim this old belongs to a worker that died, not to an upload in flight.
STALE_SEND_CLAIM_AGE = timedelta(minutes=30)
STALE_SEND_BATCH_SIZE = 200


def _public_caf_identity(element):
    """Compare CAF content, ignoring namespace and layout between elements.

    Issuers re-indent the CAF inside the TED (the SII examples do too);
    whitespace between elements carries no CAF data.
    """
    fragment = deepcopy(element)
    fragment.tail = None
    for node in fragment.iter():
        if not isinstance(node.tag, str):
            continue
        if etree.QName(node).namespace == SII[1:-1]:
            node.tag = etree.QName(node).localname
        if len(node) and node.text is not None and not node.text.strip():
            node.text = None
        if node.tail is not None and not node.tail.strip():
            node.tail = None
    etree.cleanup_namespaces(fragment)
    return etree.tostring(fragment, method="c14n", exclusive=True, with_comments=False)


class ElectronicSubmissionProduction(models.Model):
    _inherit = "l10n_cl.electronic.submission"

    transport_request_artifact_id = fields.Many2one(
        "l10n_cl.electronic.artifact",
        string="Signed Transport Envelope",
        readonly=True,
        copy=False,
        index=True,
        check_company=True,
        ondelete="restrict",
    )
    _sql_constraints = [
        (
            "submission_transport_request_uniq",
            "unique(transport_request_artifact_id)",
            "A transport envelope can belong to only one submission.",
        )
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if any(vals.get(BINDING) for vals in vals_list) or self.env.context.get(
            "default_" + BINDING
        ):
            raise UserError(
                _("Prepare the transport envelope through the submission workflow.")
            )
        return super().create([{**vals, BINDING: False} for vals in vals_list])

    def _lock_production_chain(self):
        self.ensure_one()
        self.document_id._lock_fiscal_chain()
        self.flush_recordset()
        self.env.cr.execute(
            "SELECT id FROM l10n_cl_electronic_submission WHERE id = %s FOR UPDATE",
            [self.id],
        )
        self.invalidate_recordset()

    def _has_envelope_environment(self):
        """The submission, its DTE and its company share one real SII mode."""
        return (
            self.environment in SII_ENVELOPE_ENVIRONMENTS
            and self.document_id.sii_environment == self.environment
            and self.company_id.dte_sii_environment == self.environment
        )

    def _environment_label(self):
        return environment_label(self.environment)

    def _envelope_gateway(self):
        """Return the one-DTE envelope gateway bound to this submission's mode."""
        return ProductionSiiGateway(
            document_validator_getter=self._get_document_validator,
            environment=self.environment,
        )

    def _check_request_preparation(self):
        if (
            not self._has_envelope_environment()
            or self.state != "pending"
            or self._dispatched_send_attempts()
        ):
            raise UserError(
                _(
                    "Only pending %(environment)s submissions without a send "
                    "attempt can bind an envelope."
                )
                % {"environment": self._environment_label()}
            )

    def _write_internal(self, values):
        """Binding primitive; caller must verify fiscal envelope content."""
        if BINDING not in values:
            return super()._write_internal(values)
        if not self.env.su:
            raise AccessError(
                _("Transport binding requires an authorized internal workflow.")
            )
        self._lock_production_chain()
        self._check_request_preparation()
        if (
            set(values) != {BINDING}
            or self.transport_request_artifact_id
            or not values[BINDING]
        ):
            raise UserError(
                _("A transport envelope must be bound once, without other changes.")
            )
        artifact = (
            self.env["l10n_cl.electronic.artifact"].browse(values[BINDING]).exists()
        )
        if (
            len(artifact) != 1
            or artifact.document_id != self.document_id
            or artifact.company_id != self.company_id
            or artifact.role != "sii_request"
        ):
            raise ValidationError(
                _("The transport envelope must be request evidence for this DTE.")
            )
        artifact.with_context(bin_size=False).action_verify_integrity()
        return super()._write_internal(values)

    def _check_production_operation_access(self):
        self.ensure_one()
        self.check_access("read")
        self.document_id.check_access("read")
        if self.env.su:
            return
        group = (
            "group_dte_manager"
            if self.document_id.document_code in {"56", "61"}
            else "group_dte_user"
        )
        if (
            self.company_id not in self.env.user.company_ids
            or self.company_id not in self.env.companies
            or not self.env.user.has_group("l10n_cl_dte_core." + group)
        ):
            raise AccessError(
                _("The DTE company and role must be authorized and enabled.")
            )
        source = self._production_source()
        source.check_access("read")
        check = getattr(source, "_check_dte_source_access", None)
        if check is not None:
            check(self.document_id.document_code)
        else:
            source.check_access("write")

    def _production_source(self):
        """Resolve one typed source without importing the downstream issuance addon."""
        registry = "l10n_cl.dte.source.registry"
        relations = (
            self.env[registry]._dte_source_relation_fields()
            if registry in self.env
            else ()
        )
        sources = [
            self.document_id[name] for name in relations if self.document_id[name]
        ]
        if len(sources) != 1:
            raise AccessError(_("A unique authorized DTE source is required."))
        return sources[0]

    def _prepare_production_request(self):
        """Bind the signed EnvioDTE once, before the first SII send."""
        self._check_production_operation_access()
        with self.env.cr.savepoint():
            self._lock_production_chain()
            self._check_production_operation_access()
            self._check_request_preparation()
            submission = self.sudo().with_context(bin_size=False)
            if submission.transport_request_artifact_id:
                submission._verify_production_request()
                return submission.transport_request_artifact_id
            signed = submission.document_id._get_signed_artifact()
            signed.action_verify_integrity()
            raw = submission._envelope_gateway().prepare_document(
                submission.company_id,
                submission.environment,
                base64.b64decode(signed.content, validate=True),
            )
            artifact = submission._create_response_artifact("sii_request", raw)
            submission._write_internal({BINDING: artifact.id})
            submission._verify_production_request()
            return artifact

    @contextmanager
    def _independent_transaction(self):
        """Yield this submission in its own transaction, committed on exit.

        An SII upload must never share the job's transaction: if that
        transaction were lost after SII received the envelope, the DTE would
        become pending again and a retry would upload it a second time.
        """
        if modules.module.current_test:
            # TransactionCase rows are invisible to other cursors; a savepoint
            # keeps the same commit-or-rollback boundary inside the test.
            with self.env.cr.savepoint():
                yield self
            return
        with self.env.registry.cursor() as cr:
            env = api.Environment(
                cr, self.env.uid, dict(self.env.context), su=self.env.su
            )
            yield self.with_env(env)

    def _job_send_durably(self):
        """Commit a one-use send claim, then transmit and record in a new one."""
        self.ensure_one()
        with self._independent_transaction() as submission:
            claim = submission._claim_send()
        self.invalidate_recordset()
        if not claim:
            return True
        attempt_id, request_bytes = claim
        with self._independent_transaction() as submission:
            submission._send_claimed(attempt_id, request_bytes)
        self.invalidate_recordset()
        self.document_id.invalidate_recordset()
        return True

    def _send_claimed(self, attempt_id, request_bytes):
        """Transmit a committed claim once; a stale or foreign claim is ignored."""
        self.ensure_one()
        self.invalidate_recordset()
        attempt = self.env["l10n_cl.submission.attempt"].browse(attempt_id)
        if (
            self.state != "sending"
            or attempt.submission_id != self
            or attempt.state != "started"
            or attempt.request_digest != hashlib.sha256(request_bytes).hexdigest()
        ):
            return True
        return self._dispatch_send(self._gateway(), attempt, request_bytes)

    @api.model
    def _cron_recover_interrupted_sends(self, limit=STALE_SEND_BATCH_SIZE):
        """Mark sends whose worker died after the claim as unknown; never resend."""
        cutoff = fields.Datetime.now() - STALE_SEND_CLAIM_AGE
        attempts = (
            self.env["l10n_cl.submission.attempt"]
            .sudo()
            .search(
                [
                    ("operation", "=", "send"),
                    ("state", "=", "started"),
                    ("started_at", "<", cutoff),
                    ("submission_id.state", "=", "sending"),
                    ("submission_id.environment", "!=", "demo"),
                ],
                order="started_at, id",
                limit=limit,
            )
        )
        for attempt in attempts:
            attempt.submission_id._recover_interrupted_send(attempt)
        return True

    def _recover_interrupted_send(self, attempt):
        self.ensure_one()
        submission = self.sudo()
        submission.env.cr.execute(
            "SELECT state FROM l10n_cl_electronic_submission "
            "WHERE id = %s FOR UPDATE SKIP LOCKED",
            [submission.id],
        )
        if not submission.env.cr.fetchone():
            return False
        submission.invalidate_recordset(["state"])
        attempt.invalidate_recordset(["state"])
        if submission.state != "sending" or attempt.state != "started":
            return False
        message = _(
            "The send was interrupted after it was claimed. SII may have "
            "received the envelope: review the SII portal; automatic resend "
            "is blocked."
        )
        attempt._complete(
            "unknown", error_code="interrupted_send", error_summary=message
        )
        submission._write_internal({"state": "outcome_unknown", "sii_message": message})
        submission.document_id.action_record_transition(
            "outcome_unknown",
            "sii_submission_outcome_unknown",
            message=message,
            correlation_key=submission.idempotency_key,
        )
        return True

    def _parse_production_receipt(self, artifact, request):
        header = parse_xml(request).find(f"{SII}SetDTE/{SII}Caratula")
        return parse_upload(
            base64.b64decode(
                artifact.with_context(bin_size=False).content, validate=True
            ),
            header.findtext(SII + "RutEmisor"),
            header.findtext(SII + "RutEnvia"),
        )

    def _check_production_outcome_attempt(self, attempt, operation):
        document = self.document_id
        if (
            operation != "send"
            or not self._has_envelope_environment()
            or self.state != "sending"
            or self.track_id
            or document.fiscal_state != "unknown"
            or document.preparation_state != "queued"
            or len(attempt) != 1
            or attempt.submission_id != self
            or attempt.operation != "send"
            or attempt.state != "started"
            or attempt.completed_at
            or self._dispatched_send_attempts() != attempt
            or attempt.request_digest != self.transport_request_artifact_id.sha256
        ):
            raise UserError(
                _("A unique pending %(environment)s send outcome is required.")
                % {"environment": self._environment_label()}
            )

    def _normalize_production_receipt(self, result, response, request):
        if isinstance(result, SiiTransportError):
            return {
                "state": "outcome_unknown",
                "error_code": result.code or type(result).__name__,
                "error_summary": f"dispatched={bool(result.dispatched)}; {result}",
            }
        try:
            normalized = ProductionDocumentOutcome().normalize_upload(
                result, document_code=self.document_id.document_code
            )
            parsed = self._parse_production_receipt(response, request)
            if any(
                normalized.get(key) != parsed[key]
                for key in ("state", "track_id", "transport_code")
            ):
                raise ValidationError(
                    _("The SII result differs from its retained receipt.")
                )
            return normalized
        except ValidationError as error:
            return {
                "state": "outcome_unknown",
                "error_code": type(error).__name__,
                "error_summary": str(error),
            }

    def _apply_production_outcome(self, attempt, result, operation):
        """Apply a send outcome; the caller owns the durable transaction."""
        self._check_production_operation_access()
        if not isinstance(result, Mapping | SiiTransportError):
            raise ValidationError(
                _("A transport result or transport error is required.")
            )
        with self.env.cr.savepoint():
            prefix = getattr(self.document_id, "_lock_national_fiscal_chain", None)
            if prefix is not None:
                prefix()
            self._lock_production_chain()
            self._check_production_operation_access()
            submission = self.sudo().with_context(bin_size=False)
            attempt = attempt.with_env(submission.env).exists()
            attempt._lock_for_completion()
            submission._check_production_outcome_attempt(attempt, operation)
            request = submission._verify_production_request()
            raw = (
                result.response
                if isinstance(result, SiiTransportError)
                else result.get("raw_response")
            )
            response = submission.env["l10n_cl.electronic.artifact"]
            if isinstance(raw, bytes) and raw:
                response = submission._create_response_artifact("sii_response", raw)
            outcome = submission._normalize_production_receipt(
                result, response, request
            )
            submission._record_production_outcome(attempt, outcome, response)
        return True

    def _record_production_outcome(self, attempt, outcome, response):
        state = outcome["state"]
        diagnostic = str(outcome.get("error_summary") or "")[:4000]
        attempt._complete(
            "unknown" if state == "outcome_unknown" else "completed",
            response_digest=response.sha256,
            error_code=str(
                outcome.get("error_code") or outcome.get("transport_code") or ""
            )[:128],
            error_summary=diagnostic,
        )
        self.document_id.action_record_transition(
            state,
            f"sii_upload_{state}",
            message=diagnostic,
            correlation_key=self.idempotency_key,
            artifact_id=response.id,
        )
        values = {"state": state}
        if state == "submitted":
            values.update(
                track_id=outcome["track_id"], submitted_at=fields.Datetime.now()
            )
        self._write_internal(values)

    def _verify_production_request(self):
        """Verify retained evidence; never reconstruct or regenerate the request."""
        self._check_production_operation_access()
        submission = self.sudo().with_context(bin_size=False)
        document = submission.document_id
        artifact = submission.transport_request_artifact_id
        if (
            not artifact
            or artifact.document_id != document
            or artifact.company_id != submission.company_id
            or artifact.role != "sii_request"
        ):
            raise ValidationError(
                _("The transport envelope must be request evidence for this DTE.")
            )
        if submission.environment != document.sii_environment:
            raise ValidationError(_("The submission must retain its DTE environment."))
        artifact.action_verify_integrity()
        signed = document._get_signed_artifact()
        signed.action_verify_integrity()
        original = base64.b64decode(signed.content, validate=True)
        raw = base64.b64decode(artifact.content, validate=True)
        submission._envelope_gateway().validate_request(
            submission.company_id, submission.environment, raw
        )
        payload = validate_document_payload(
            original,
            submission._get_document_validator,
            allowed_codes={document.document_code},
            strict_sii=True,
        )
        header = payload.find(SII + "Encabezado")
        dd = payload.find(f"{SII}TED/{SII}DD")
        issuer = normalize_rut(submission.company_id.vat)
        if (
            header.findtext(f"{SII}IdDoc/{SII}Folio") != str(document.folio)
            or normalize_rut(header.findtext(f"{SII}Emisor/{SII}RUTEmisor")) != issuer
            or dd.findtext(SII + "F") != str(document.folio)
            or dd.findtext(SII + "TD") != document.document_code
            or normalize_rut(dd.findtext(SII + "RE")) != issuer
        ):
            raise ValidationError(
                _("The signed DTE identity differs from the submission.")
            )
        dtes = parse_xml(raw).findall(f"{SII}SetDTE/{SII}DTE")
        if (
            len(dtes) != 1
            or _without_xml_declaration(original) not in raw
            or etree.tostring(dtes[0], method="c14n", exclusive=True)
            != etree.tostring(payload.getparent(), method="c14n", exclusive=True)
        ):
            raise ValidationError(
                _("The envelope must contain this exact signed DTE only.")
            )
        folio = submission.env["l10n_cl.caf.folio"].search(
            [("document_id", "=", document.id)]
        )
        caf = folio.authorization_id
        if (
            len(folio) != 1
            or folio.state == "void"
            or folio.number != document.folio
            or caf.is_synthetic
            or caf.sii_environment != submission.environment
            or caf.company_id != submission.company_id
            or caf.document_code != document.document_code
        ):
            raise ValidationError(
                _("The request requires its non-synthetic %(environment)s CAF.")
                % {"environment": submission._environment_label()}
            )
        caf._check_caf_integrity()
        embedded = payload.findall(f"{SII}TED/{SII}DD/{SII}CAF")
        reserved = parse_xml(base64.b64decode(caf.caf_file, validate=True)).find("CAF")
        if (
            len(embedded) != 1
            or reserved is None
            or _public_caf_identity(embedded[0]) != _public_caf_identity(reserved)
        ):
            raise ValidationError(
                _("The signed TED must contain the reserved %(environment)s CAF.")
                % {"environment": submission._environment_label()}
            )
        return raw
