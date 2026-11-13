# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Orchestrate durable CAF acquisition, validation and reconciliation."""

import base64
import hashlib
import logging

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from odoo import SUPERUSER_ID, _, api, fields
from odoo.exceptions import UserError, ValidationError

from .certification_caf_diagnostics import _diagnostic, _safe_code
from .sii_certification_caf_client import (
    CafAllocationRejected,
    CafDispatchUncertain,
    CafRequestRejected,
    CafSessionExpired,
    CafTransportUnavailable,
    SiiCertificationCafClient,
)

_logger = logging.getLogger(__name__)


class CertificationCafService:
    """Process a CAF request without persisting session or private-key data."""

    def __init__(self, env, client_factory=SiiCertificationCafClient):
        self.env = env
        self.client_factory = client_factory

    def start_request(self, request, already_claimed=False):
        """Prepare one line and defer the irreversible POST until commit."""
        request.ensure_one()
        request._check_request_access()
        if not already_claimed:
            self.env.cr.execute(
                "SELECT id FROM l10n_cl_dte_certification_caf_request "
                "WHERE id = %s AND state = 'queued' FOR UPDATE",
                [request.id],
            )
            if not self.env.cr.fetchone():
                raise UserError(_("This CAF request is already being processed."))
            request.invalidate_recordset(["state"])
            request._write_internal(
                {"state": "running", "started_at": fields.Datetime.now()}
            )
        lines = request.line_ids.filtered(lambda line: line.state == "queued").sorted(
            key=lambda line: (line.document_code, line.id)
        )
        if not lines:
            raise UserError(_("This CAF request has no queued lines."))
        client = None
        batch_owned = False
        try:
            certificate_pem, private_key_pem = self._certificate_material(request)
            client = self.client_factory()
            session = client.authenticate(certificate_pem, private_key_pem)
            batch = _CafBatch(
                registry=self.env.registry,
                client=client,
                session=session,
                request_id=request.id,
                company_vat=request.company_id.vat,
                certificate_pem=certificate_pem,
                private_key_pem=private_key_pem,
            )
            batch_owned = self._prepare_line(request, lines[0], batch)
        except CafTransportUnavailable as error:
            self._fail_before_dispatch(request, lines[0], str(error))
        except CafAllocationRejected as error:
            self._record_quantity_limit(lines[0], error.maximum_quantity)
            self._fail_before_dispatch(request, lines[0], error.code)
        except CafRequestRejected as error:
            self._fail_before_dispatch(
                request, lines[0], getattr(error, "code", str(error))
            )
        except Exception as error:  # pylint: disable=broad-except
            _logger.warning(
                "CAF request %s failed before dispatch (%s)",
                request.id,
                type(error).__name__,
            )
            self._fail_before_dispatch(request, lines[0], "pre_dispatch_failed")
        finally:
            # Ownership transfers to _CafBatch only after successful preparation.
            # The client is otherwise released here, including failed mTLS setup.
            if client is not None and not batch_owned:
                client.close()

    def _prepare_line(self, request, line, batch):
        """Navigate the assistant and durably mark the range before its POST."""
        try:
            prepared = batch.client.prepare_request(
                batch.session,
                batch.company_vat,
                line.document_code,
                line.requested_quantity,
            )
        except CafSessionExpired:
            batch.session = batch.client.authenticate(
                batch.certificate_pem, batch.private_key_pem
            )
            prepared = batch.client.prepare_request(
                batch.session,
                batch.company_vat,
                line.document_code,
                line.requested_quantity,
            )
        except CafAllocationRejected as error:
            self._record_quantity_limit(line, error.maximum_quantity)
            self._fail_before_dispatch(request, line, error.code)
            return False
        except CafRequestRejected as error:
            self._fail_before_dispatch(
                request, line, getattr(error, "code", str(error))
            )
            return False
        except Exception as error:  # pylint: disable=broad-except
            _logger.warning(
                "CAF request %s line %s failed before dispatch (%s)",
                request.id,
                line.id,
                type(error).__name__,
            )
            self._fail_before_dispatch(request, line, "pre_dispatch_failed")
            return False

        _action, _form, _referer, _rut, _dv, folio_start, folio_end = prepared
        line._write_internal(
            {
                "state": "dispatching",
                "folio_start": folio_start,
                "folio_end": folio_end,
                "started_at": fields.Datetime.now(),
                "attempt_count": line.attempt_count + 1,
            }
        )
        request._write_internal(
            {
                "state": "running",
                "diagnostic_code": False,
                "diagnostic_message": False,
            }
        )
        self.env.cr.postcommit.add(
            lambda: batch.dispatch(line.id, prepared, line.document_code)
        )
        self.env.cr.postrollback.add(batch.client.close)
        return True

    def _fail_before_dispatch(self, request, line, code):
        """Persist a safe failure while no folio-generation POST was attempted."""
        code = _safe_code(code)
        message = (
            self.env._(
                "SII has reached its limit of authenticated sessions (code 27). "
                "Close an existing SII session or wait one hour from the last "
                "successful login before trying again."
            )
            if code == "sii_session_limit_exceeded"
            else self.env._(
                "The requested quantity exceeds the maximum authorized by SII. "
                "Reduce the quantity before submitting; no folios were allocated."
            )
            if code == "sii_quantity_limit_exceeded"
            else _diagnostic(code)
        )
        line._write_internal(
            {
                "state": "failed",
                "error_code": code,
                "error_message": message,
                "completed_at": fields.Datetime.now(),
            }
        )
        request._write_internal(
            {
                "state": "failed",
                "completed_at": fields.Datetime.now(),
                "diagnostic_code": code,
                "diagnostic_message": message,
            }
        )

    @staticmethod
    def _record_quantity_limit(line, maximum):
        if maximum is not None:
            line._write_internal(
                {
                    "sii_maximum_known": True,
                    "sii_maximum_quantity": maximum,
                }
            )

    def _certificate_material(self, request):
        """Read and validate the configured certificate PEM material in memory."""
        company = request.company_id
        if (
            company.dte_sii_environment != "certification"
            or not company.dte_certification_enabled
        ):
            raise UserError(_("CAF requests are available only in Certification."))
        profile = (
            self.env["l10n_cl.dte.signing.profile"]
            .sudo()
            .search(
                [
                    ("company_id", "=", company.id),
                    ("sii_environment", "=", "certification"),
                    ("active", "=", True),
                ],
                limit=1,
            )
        )
        certificate = profile.certificate_id
        key_record = certificate.private_key_id
        if (
            not profile
            or profile.is_synthetic
            or not certificate
            or certificate.company_id != company
            or not certificate.active
            or not certificate.is_valid
            or not key_record
            or key_record.public
            or key_record.loading_error
        ):
            raise UserError(
                _("Configure a valid, non-synthetic Certification signing profile.")
            )
        chain = certificate._get_certificate_chain()
        if not chain or chain[0] != certificate:
            raise ValidationError(_("The configured certificate chain is invalid."))
        pem_chain = []
        for chain_certificate in chain:
            encoded_certificate = chain_certificate.with_context(
                bin_size=False
            ).pem_certificate
            if not encoded_certificate:
                raise ValidationError(
                    _("The configured certificate chain is incomplete.")
                )
            pem_chain.append(base64.b64decode(encoded_certificate, validate=True))
        encoded_key = key_record.with_context(bin_size=False).pem_key
        if not encoded_key:
            raise ValidationError(_("The signing profile has no private key."))
        private_key_pem = base64.b64decode(encoded_key, validate=True)
        private_key = serialization.load_pem_private_key(private_key_pem, password=None)
        leaf_certificate = x509.load_pem_x509_certificate(pem_chain[0])
        key_public = private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        cert_public = leaf_certificate.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if key_public != cert_public:
            raise ValidationError(
                _("The signing profile private key does not match its certificate.")
            )
        return b"\n".join(pem_chain), private_key_pem

    def reconcile_line(self, line, caf, note):
        """Link a manually recovered CAF to an uncertain line without network I/O."""
        line.ensure_one()
        caf.ensure_one()
        line.request_id._check_request_access()
        if line.state != "uncertain":
            raise UserError(_("Only an uncertain CAF request can be reconciled."))
        if not note or not note.strip():
            raise ValidationError(_("Enter an operational reconciliation note."))
        line.request_id.set_id._lock_configuration()
        if not line.request_id.set_id.can_edit_envelope:
            raise UserError(
                _("Reconcile CAF requests before generating documents for this set.")
            )
        if (
            caf.company_id != line.company_id
            or caf.sii_environment != "certification"
            or caf.is_synthetic
            or caf.state not in {"validated", "exhausted"}
            or caf.document_code != line.document_code
            or caf.folio_start != line.folio_start
            or caf.folio_end != line.folio_end
        ):
            raise ValidationError(
                _(
                    "The recovered CAF must match the company, Certification "
                    "environment, document type and exact expected range."
                )
            )
        caf._check_caf_integrity()
        existing = line.request_id.set_id.caf_line_ids.filtered(
            lambda relation: relation.caf_id == caf
        )
        if not existing:
            same_type = line.request_id.set_id.caf_line_ids.filtered(
                lambda relation: relation.document_code == line.document_code
            )
            self.env["l10n_cl.dte.certification.set.caf"].create(
                {
                    "set_id": line.request_id.set_id.id,
                    "caf_id": caf.id,
                    "role": "additional" if same_type else "primary",
                    "sequence": max(same_type.mapped("sequence"), default=0) + 10,
                }
            )
        line._write_internal(
            {
                "state": "reconciled",
                "caf_id": caf.id,
                "caf_digest": caf.caf_digest,
                "reconciled_by": self.env.uid,
                "reconciled_at": fields.Datetime.now(),
                "reconciliation_note": note.strip()[:500],
                "completed_at": fields.Datetime.now(),
                "error_code": False,
                "error_message": False,
            }
        )
        request = line.request_id
        states = request.line_ids.mapped("state")
        if "uncertain" not in states and "dispatching" not in states:
            if "queued" in states:
                request._write_internal(
                    {
                        "state": "queued",
                        "completed_at": False,
                        "diagnostic_code": False,
                        "diagnostic_message": False,
                    }
                )
            else:
                request._write_internal(
                    {
                        "state": "reconciled",
                        "completed_at": fields.Datetime.now(),
                        "diagnostic_code": False,
                        "diagnostic_message": False,
                    }
                )
        return True

    def recover_line(self, line):
        """Recover an exact authorized CAF and reconcile it without new allocation."""
        line.ensure_one()
        request = line.request_id
        request._check_request_access()
        request.set_id._lock_configuration()
        self.env.cr.execute(
            "SELECT id FROM l10n_cl_dte_certification_caf_request_line "
            "WHERE id = %s FOR UPDATE",
            [line.id],
        )
        line.invalidate_recordset()
        if line.state == "reconciled" and line.caf_id:
            return line.caf_id
        if line.state != "uncertain":
            raise UserError(_("Only uncertain CAF requests can be recovered."))
        if not request.set_id.can_edit_envelope:
            raise UserError(_("Recover CAFs before generating documents for this set."))
        line._write_internal(
            {
                "recovery_attempt_count": line.recovery_attempt_count + 1,
                "last_recovery_at": fields.Datetime.now(),
            }
        )
        client = None
        try:
            with self.env.cr.savepoint():
                authorization = self.env["l10n_cl.caf.authorization"].search(
                    [
                        ("company_id", "=", request.company_id.id),
                        ("sii_environment", "=", "certification"),
                        ("document_code", "=", line.document_code),
                        ("folio_start", "=", line.folio_start),
                        ("folio_end", "=", line.folio_end),
                        ("state", "in", ["validated", "exhausted"]),
                        ("is_synthetic", "=", False),
                    ],
                    limit=1,
                )
                if not authorization:
                    certificate, private_key = self._certificate_material(request)
                    client = self.client_factory()
                    session = client.authenticate(certificate, private_key)
                    try:
                        result = client.recover_caf(
                            session,
                            request.company_id.vat,
                            line.document_code,
                            line.folio_start,
                            line.folio_end,
                        )
                    except CafSessionExpired:
                        session = client.authenticate(certificate, private_key)
                        result = client.recover_caf(
                            session,
                            request.company_id.vat,
                            line.document_code,
                            line.folio_start,
                            line.folio_end,
                        )
                    del certificate, private_key
                    authorization = self._register_recovered_caf(line, result)
                self.reconcile_line(
                    line,
                    authorization,
                    _(
                        "CAF recovered and verified automatically for the "
                        "recorded range."
                    ),
                )
                line._write_internal(
                    {
                        "recovery_error_code": False,
                        "recovery_message": _(
                            "CAF recovered and reconciled successfully."
                        ),
                    }
                )
                return authorization
        except (CafRequestRejected, CafTransportUnavailable) as error:
            code = getattr(error, "code", str(error))
            if code == "caf_recovery_not_found" and client is not None:
                try:
                    proposed = client.prepare_request(
                        session,
                        request.company_id.vat,
                        line.document_code,
                        line.requested_quantity,
                        check_quantity_limit=False,
                    )
                    if (
                        isinstance(proposed, tuple)
                        and len(proposed) == 7
                        and proposed[-2:] == (line.folio_start, line.folio_end)
                    ):
                        maximum = proposed[1]["inputs"].get("MAX_AUTOR", "")
                        if maximum.isdigit():
                            self._record_quantity_limit(line, int(maximum))
                        code = "caf_allocation_not_completed"
                        message = self._recovery_diagnostic(code)
                        line._write_internal(
                            {
                                "state": "failed",
                                "error_code": code,
                                "error_message": message,
                                "recovery_error_code": code,
                                "recovery_message": message,
                            }
                        )
                        states = request.line_ids.mapped("state")
                        request._write_internal(
                            {
                                "state": (
                                    "uncertain"
                                    if "uncertain" in states
                                    else "partial"
                                    if {"success", "reconciled"}.intersection(states)
                                    else "failed"
                                ),
                                "diagnostic_code": code,
                                "diagnostic_message": message,
                            }
                        )
                        return self.env["l10n_cl.caf.authorization"]
                except Exception as verification_error:  # pylint: disable=broad-except
                    _logger.info(
                        "CAF non-allocation verification for line %s stopped (%s)",
                        line.id,
                        type(verification_error).__name__,
                    )
            if code not in {
                "caf_recovery_not_found",
                "caf_recovery_ambiguous",
                "caf_recovery_identity_mismatch",
                "sii_session_limit_exceeded",
            }:
                code = "caf_recovery_failed"
        except Exception as error:  # pylint: disable=broad-except
            _logger.warning(
                "CAF recovery for request %s line %s failed (%s)",
                request.id,
                line.id,
                type(error).__name__,
            )
            code = "caf_recovery_failed"
        finally:
            if client is not None:
                client.close()
        line._write_internal(
            {
                "recovery_error_code": code,
                "recovery_message": self._recovery_diagnostic(code),
            }
        )
        return self.env["l10n_cl.caf.authorization"]

    def _recovery_diagnostic(self, code):
        messages = {
            "sii_session_limit_exceeded": self.env._(
                "SII has reached its limit of authenticated sessions (code 27). "
                "Close an existing SII session or wait one hour from the last "
                "successful login before trying again."
            ),
            "caf_allocation_not_completed": _(
                "SII does not list this range as authorized and still offers "
                "the same range for allocation. No CAF was issued for this attempt."
            ),
            "caf_recovery_not_found": _(
                "The recorded range does not appear in SII authorized folios. "
                "No CAF was downloaded; the request remains uncertain."
            ),
            "caf_recovery_ambiguous": _(
                "SII returned more than one matching range. No CAF was linked."
            ),
            "caf_recovery_identity_mismatch": _(
                "The recovered CAF does not match the company, document type or "
                "exact recorded range. The request remains uncertain."
            ),
            "caf_recovery_failed": _(
                "The authorized CAF could not be recovered. The request remains "
                "uncertain; use Recover CAF from SII to check again."
            ),
        }
        return messages[code]

    def _register_recovered_caf(self, line, result):
        request = line.request_id
        model = self.env["l10n_cl.caf.authorization"]
        if (
            result.idk != "100"
            or model._normalize_vat(result.issuer_vat)
            != model._normalize_vat(request.company_id.vat)
            or result.document_code != line.document_code
            or result.folio_start != line.folio_start
            or result.folio_end != line.folio_end
        ):
            raise CafRequestRejected("caf_recovery_identity_mismatch")
        authorization = model.create(
            {
                "name": f"CAF-{result.document_code}-{result.folio_start}.xml",
                "company_id": request.company_id.id,
                "sii_environment": "certification",
                "is_synthetic": False,
                "document_code": result.document_code,
                "issuer_vat": result.issuer_vat,
                "folio_start": result.folio_start,
                "folio_end": result.folio_end,
                "date_from": result.date_from,
                "caf_file": base64.b64encode(result.content),
                "caf_filename": f"CAF-{result.document_code}-{result.folio_start}.xml",
            }
        )
        authorization.action_validate_authorization()
        return authorization


class _CafBatch:
    """In-memory worker state retained only by Odoo's post-commit callbacks."""

    def __init__(
        self,
        registry,
        client,
        session,
        request_id,
        company_vat,
        certificate_pem,
        private_key_pem,
    ):
        self.registry = registry
        self.client = client
        self.session = session
        self.request_id = request_id
        self.company_vat = company_vat
        self.certificate_pem = certificate_pem
        self.private_key_pem = private_key_pem

    def dispatch(self, line_id, prepared, document_code):
        """Send the irreversible POST and persist the outcome in a new transaction."""
        try:
            result = self.client.dispatch_and_download(
                self.session,
                prepared,
                self.company_vat,
                document_code,
                before_dispatch=lambda _start, _end: None,
            )
            self._persist_success(line_id, result)
        except CafAllocationRejected as error:
            self._persist_rejection(line_id, error)
        except CafDispatchUncertain as error:
            self._persist_uncertain(line_id, error.code)
        except Exception as error:  # pylint: disable=broad-except
            _logger.warning(
                "CAF request %s line %s has an uncertain dispatch outcome (%s)",
                self.request_id,
                line_id,
                type(error).__name__,
            )
            self._persist_uncertain(line_id, "dispatch_outcome_unknown")

    def _persist_rejection(self, line_id, error):
        with self.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            line = env["l10n_cl.dte.certification.caf.request.line"].browse(line_id)
            if line.state != "dispatching":
                cr.postcommit.add(self.client.close)
                return
            request = line.request_id
            message = env(context={"lang": request.requested_by.lang})._(
                "SII did not complete the request because the quantity exceeds "
                "its authorized maximum. No CAF was issued."
            )
            CertificationCafService._record_quantity_limit(line, error.maximum_quantity)
            line._write_internal(
                {
                    "state": "failed",
                    "error_code": error.code,
                    "error_message": message,
                    "completed_at": fields.Datetime.now(),
                }
            )
            request._write_internal(
                {
                    "diagnostic_code": error.code,
                    "diagnostic_message": message,
                    "completed_at": fields.Datetime.now(),
                }
            )
            self._update_request_state(request)
        self.client.close()

    def _persist_success(self, line_id, result):
        with self.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            line = env["l10n_cl.dte.certification.caf.request.line"].browse(line_id)
            request = line.request_id
            line.invalidate_recordset()
            if line.state != "dispatching":
                cr.postcommit.add(self.client.close)
                return
            try:
                with cr.savepoint():
                    authorization = env["l10n_cl.caf.authorization"].create(
                        {
                            "name": (
                                f"CAF-{result.document_code}-{result.folio_start}.xml"
                            ),
                            "company_id": request.company_id.id,
                            "sii_environment": "certification",
                            "is_synthetic": False,
                            "document_code": result.document_code,
                            "issuer_vat": result.issuer_vat,
                            "folio_start": result.folio_start,
                            "folio_end": result.folio_end,
                            "date_from": result.date_from,
                            "caf_file": base64.b64encode(result.content),
                            "caf_filename": (
                                f"CAF-{result.document_code}-{result.folio_start}.xml"
                            ),
                        }
                    )
                    authorization.action_validate_authorization()
                    same_type = request.set_id.caf_line_ids.filtered(
                        lambda relation: relation.document_code == result.document_code
                    )
                    role = "additional" if same_type else "primary"
                    env["l10n_cl.dte.certification.set.caf"].create(
                        {
                            "set_id": request.set_id.id,
                            "caf_id": authorization.id,
                            "role": role,
                            "sequence": max(same_type.mapped("sequence"), default=0)
                            + 10,
                        }
                    )
            except Exception as error:  # pylint: disable=broad-except
                _logger.warning(
                    "CAF request %s line %s failed local validation (%s)",
                    self.request_id,
                    line_id,
                    type(error).__name__,
                )
                line._write_internal(
                    {
                        "state": "uncertain",
                        "error_code": "caf_validation_after_dispatch",
                        "error_message": _diagnostic("caf_validation_after_dispatch"),
                        "completed_at": fields.Datetime.now(),
                    }
                )
                request._write_internal(
                    {
                        "state": "uncertain",
                        "completed_at": fields.Datetime.now(),
                        "diagnostic_code": "caf_validation_after_dispatch",
                        "diagnostic_message": _diagnostic(
                            "caf_validation_after_dispatch"
                        ),
                    }
                )
                cr.postcommit.add(self.client.close)
                cr.postcommit.add(lambda: self._recover_uncertain_line(line_id))
                return
            line._write_internal(
                {
                    "state": "success",
                    "caf_id": authorization.id,
                    "caf_digest": hashlib.sha256(result.content).hexdigest(),
                    "completed_at": fields.Datetime.now(),
                    "error_code": False,
                    "error_message": False,
                }
            )
            self._update_request_state(request)
            if request.state == "running":
                cr.postcommit.add(lambda: self.prepare_next())
        self._close_if_complete()

    def _persist_uncertain(self, line_id, code):
        code = _safe_code(code)
        with self.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            line = env["l10n_cl.dte.certification.caf.request.line"].browse(line_id)
            if line.state != "dispatching":
                cr.postcommit.add(self.client.close)
                return
            request = line.request_id
            line._write_internal(
                {
                    "state": "uncertain",
                    "error_code": code,
                    "error_message": _diagnostic(code),
                    "completed_at": fields.Datetime.now(),
                }
            )
            request._write_internal(
                {
                    "state": "uncertain",
                    "completed_at": fields.Datetime.now(),
                    "diagnostic_code": code,
                    "diagnostic_message": _diagnostic(code),
                }
            )
            cr.postcommit.add(self.client.close)
            cr.postcommit.add(lambda: self._recover_uncertain_line(line_id))
        self._close_if_complete()

    def _recover_uncertain_line(self, line_id):
        """Try re-download once after uncertainty is durably recorded."""
        try:
            with self.registry.cursor() as cr:
                root_env = api.Environment(cr, SUPERUSER_ID, {})
                root_line = root_env[
                    "l10n_cl.dte.certification.caf.request.line"
                ].browse(line_id)
                request = root_line.request_id
                actor = request.requested_by
                env = api.Environment(
                    cr,
                    actor.id,
                    {
                        "allowed_company_ids": [request.company_id.id],
                        "lang": actor.lang,
                    },
                )
                line = env["l10n_cl.dte.certification.caf.request.line"].browse(line_id)
                CertificationCafService(env).recover_line(line)
        except Exception as error:  # pylint: disable=broad-except
            _logger.warning(
                "Automatic CAF recovery for line %s stopped (%s)",
                line_id,
                type(error).__name__,
            )

    def _update_request_state(self, request):
        states = request.line_ids.mapped("state")
        if "uncertain" in states:
            state = "uncertain"
        elif "failed" in states:
            state = (
                "partial" if "success" in states or "reconciled" in states else "failed"
            )
        elif "queued" in states or "dispatching" in states:
            state = "running"
        elif "reconciled" in states:
            state = "reconciled"
        else:
            state = "success"
        values = {"state": state}
        if state not in {"queued", "running"}:
            values["completed_at"] = fields.Datetime.now()
        request._write_internal(values)

    def prepare_next(self):
        """Prepare the next queued line while retaining this batch's live session."""
        with self.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            request = env["l10n_cl.dte.certification.caf.request"].browse(
                self.request_id
            )
            if request.state != "running":
                self._close_if_complete()
                return
            line = request.line_ids.filtered(
                lambda item: item.state == "queued"
            ).sorted(key=lambda item: (item.document_code, item.id))[:1]
            if not line:
                self._update_request_state(request)
                if request.state == "running":
                    request._write_internal(
                        {"state": "success", "completed_at": fields.Datetime.now()}
                    )
                cr.postcommit.add(self._close_if_complete)
                return
            try:
                prepared = self.client.prepare_request(
                    self.session,
                    self.company_vat,
                    line.document_code,
                    line.requested_quantity,
                )
            except CafSessionExpired:
                try:
                    self.session = self.client.authenticate(
                        self.certificate_pem, self.private_key_pem
                    )
                    prepared = self.client.prepare_request(
                        self.session,
                        self.company_vat,
                        line.document_code,
                        line.requested_quantity,
                    )
                except CafRequestRejected as error:
                    CertificationCafService._record_quantity_limit(
                        line, getattr(error, "maximum_quantity", None)
                    )
                    self._fail_in_cursor(cr, request, line, error.code)
                    self._close_if_complete()
                    return
                except Exception as error:  # pylint: disable=broad-except
                    self._fail_in_cursor(
                        cr, request, line, "session_expired_before_dispatch"
                    )
                    _logger.warning(
                        "CAF request %s stopped before dispatch (%s)",
                        self.request_id,
                        type(error).__name__,
                    )
                    self._close_if_complete()
                    return
            except CafRequestRejected as error:
                CertificationCafService._record_quantity_limit(
                    line, getattr(error, "maximum_quantity", None)
                )
                self._fail_in_cursor(cr, request, line, error.code)
                self._close_if_complete()
                return
            except Exception as error:  # pylint: disable=broad-except
                self._fail_in_cursor(cr, request, line, "pre_dispatch_failed")
                _logger.warning(
                    "CAF request %s stopped before dispatch (%s)",
                    self.request_id,
                    type(error).__name__,
                )
                self._close_if_complete()
                return

            _action, _form, _referer, _rut, _dv, folio_start, folio_end = prepared
            line._write_internal(
                {
                    "state": "dispatching",
                    "folio_start": folio_start,
                    "folio_end": folio_end,
                    "started_at": fields.Datetime.now(),
                    "attempt_count": line.attempt_count + 1,
                }
            )
            cr.postcommit.add(
                lambda: self.dispatch(line.id, prepared, line.document_code)
            )
            cr.postrollback.add(self.client.close)

    def _fail_in_cursor(self, cr, request, line, code):
        code = _safe_code(code)
        message = (
            request.with_context(lang=request.requested_by.lang).env._(
                "SII has reached its limit of authenticated sessions (code 27). "
                "Close an existing SII session or wait one hour from the last "
                "successful login before trying again."
            )
            if code == "sii_session_limit_exceeded"
            else request.with_context(lang=request.requested_by.lang).env._(
                "The requested quantity exceeds the maximum authorized by SII. "
                "Reduce the quantity before submitting; no folios were allocated."
            )
            if code == "sii_quantity_limit_exceeded"
            else _diagnostic(code)
        )
        line._write_internal(
            {
                "state": "failed",
                "error_code": code,
                "error_message": message,
                "completed_at": fields.Datetime.now(),
            }
        )
        self._update_request_state(request)
        request._write_internal(
            {"diagnostic_code": code, "diagnostic_message": message}
        )
        if request.state not in {"queued", "running"}:
            cr.postcommit.add(self._close_if_complete)

    def _close_if_complete(self):
        with self.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            request = env["l10n_cl.dte.certification.caf.request"].browse(
                self.request_id
            )
            if request.state not in {"queued", "running"}:
                self.client.close()
