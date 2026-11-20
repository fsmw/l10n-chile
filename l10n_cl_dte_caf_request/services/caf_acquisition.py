# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Durable execution of one CAF request attempt.

Every state change is committed in its own cursor so that a crash or a
rollback after the irreversible SII request can never return the request to a
dispatchable state: the attempt is ``running`` before the network call, it is
marked as dispatched, with the folio range reserved by the SII assistant,
right before the POST that allocates the folios, and its outcome is recorded
afterwards. The network call runs outside any transaction that holds a lock on
the request, and never inside invoice, numbering or signing transactions.
"""

import base64
import hashlib
import logging
from contextlib import contextmanager

from psycopg2 import IntegrityError, errors

from odoo import fields
from odoo.exceptions import UserError

from .caf_errors import POST_DISPATCH, PRE_DISPATCH, VALIDATION, SiiCafError
from .caf_messages import error_message

_logger = logging.getLogger(__name__)


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def activate_caf(env, request, result):
    """Register a downloaded CAF in ``l10n_cl_dte_caf`` and validate it.

    Raises ``UserError``/``ValidationError`` or ``IntegrityError`` when the CAF
    cannot be activated; callers run it inside a savepoint.
    """
    authorization = env["l10n_cl.caf.authorization"].create(
        {
            "name": (
                f"CAF {result.document_code} {result.folio_start}-{result.folio_end}"
            ),
            "company_id": request.company_id.id,
            "sii_environment": request.environment,
            "is_synthetic": False,
            "document_code": result.document_code,
            "issuer_vat": result.issuer_vat,
            "folio_start": result.folio_start,
            "folio_end": result.folio_end,
            "date_from": result.date_from,
            "caf_file": base64.b64encode(result.content),
            "caf_filename": (
                f"CAF-{result.document_code}-{result.folio_start}"
                f"-{result.folio_end}.xml"
            ),
        }
    )
    authorization.action_validate_authorization()
    return authorization


def create_evidence(env, request, attempt, result):
    """Store the exact SII download response, which is the CAF itself."""
    content = result.content
    filename = f"CAF-{result.document_code}-{result.folio_start}-{result.folio_end}"
    return (
        env["l10n_cl.caf.request.evidence"]
        .sudo()
        .create(
            {
                "request_id": request.id,
                "attempt_id": attempt.id,
                "environment": request.environment,
                "endpoint": attempt.endpoint,
                "http_status": 200,
                "response_file": base64.b64encode(content),
                "response_filename": f"{filename}-sii-response.xml",
                "response_digest": sha256(content),
                "response_size": len(content),
                "caf_file": base64.b64encode(content),
                "caf_filename": f"{filename}.xml",
                "caf_digest": sha256(content),
            }
        )
    )


def validation_failure_text(error):
    # Validation messages are fixed texts of l10n_cl_dte_caf.
    return str(error)[:500] if isinstance(error, UserError) else "CAF_DUPLICATED"


class CafAcquisition:
    """Run one attempt of one ``l10n_cl.caf.request`` against the SII."""

    def __init__(self, request, client):
        request.ensure_one()
        self.request = request
        self.request_id = request.id
        self.client = client
        self.dispatched = False

    @contextmanager
    def _env(self):
        """Yield the caller's environment on a new, independently committed cursor."""
        with self.request.env.registry.cursor() as cr:
            env = self.request.env(cr=cr)
            yield env, env["l10n_cl.caf.request"].browse(self.request_id)

    def run(self, allowed_states, check=None):
        request = self.request
        request.env.flush_all()
        attempt_id = self._start(allowed_states, check)
        try:
            result = self.client.request_caf(
                request.company_id,
                request.document_code,
                request.requested_quantity,
                before_dispatch=lambda start, end: self._mark_dispatched(
                    attempt_id, start, end
                ),
            )
        except SiiCafError as error:
            self._finish_error(attempt_id, error)
        except Exception as error:  # pylint: disable=broad-except
            # The exception text may come from a transport; keep only its type.
            _logger.warning(
                "CAF request %s stopped unexpectedly (%s)",
                self.request_id,
                type(error).__name__,
            )
            self._finish_error(attempt_id, self._unexpected())
        else:
            self._finish_success(attempt_id, result)
        request.env.invalidate_all()
        return request

    def _unexpected(self):
        if self.dispatched:
            return SiiCafError("SII_DISPATCH_UNCERTAIN", POST_DISPATCH, uncertain=True)
        return SiiCafError("UNEXPECTED_ERROR", PRE_DISPATCH)

    def _start(self, allowed_states, check):
        with self._env() as (env, request):
            try:
                env.cr.execute(
                    "SELECT id FROM l10n_cl_caf_request "
                    "WHERE id = %s FOR UPDATE NOWAIT",
                    [request.id],
                )
            except errors.LockNotAvailable:
                raise UserError(
                    env._("This CAF request is already being processed.")
                ) from None
            request.invalidate_recordset()
            if request.state not in allowed_states:
                raise UserError(
                    env._("The CAF request cannot run in its current state.")
                )
            if check:
                check(request)
            number = request.attempt_count + 1
            now = fields.Datetime.now()
            attempt = (
                env["l10n_cl.caf.request.attempt"]
                .sudo()
                .create(
                    {
                        "request_id": request.id,
                        "number": number,
                        "executed_by": env.uid,
                        "started_at": now,
                        "endpoint": self.client.endpoint,
                    }
                )
            )
            request._write_internal(
                {
                    "state": "running",
                    "attempt_count": number,
                    "requested_at": now,
                    "error_code": False,
                    "error_message": False,
                    "failure_phase": False,
                    "sii_error_code": False,
                }
            )
            return attempt.id

    def _mark_dispatched(self, attempt_id, folio_start, folio_end):
        """Durably record the reserved range before the allocation POST."""
        values = {"folio_start": folio_start, "folio_end": folio_end}
        with self._env() as (env, request):
            env["l10n_cl.caf.request.attempt"].sudo().browse(attempt_id).write(
                {**values, "dispatch_started": True}
            )
            request._write_internal(values)
        self.dispatched = True

    def _finish_error(self, attempt_id, error):
        state = "uncertain" if error.uncertain else "failed"
        with self._env() as (env, request):
            attempt = env["l10n_cl.caf.request.attempt"].sudo().browse(attempt_id)
            message = error_message(env, error.code)
            request_values = {"sii_error_code": error.sii_code or False}
            if error.maximum_quantity:
                request_values["sii_maximum_quantity"] = error.maximum_quantity
            self._close(
                request,
                attempt,
                state,
                {
                    "error_code": error.code,
                    "error_message": message,
                    "failure_phase": error.phase,
                    "dispatch_started": error.phase != PRE_DISPATCH,
                },
                request_values,
            )
            request._notify_operator(error.code, message)

    def _finish_success(self, attempt_id, result):
        with self._env() as (env, request):
            attempt = env["l10n_cl.caf.request.attempt"].sudo().browse(attempt_id)
            evidence = create_evidence(env, request, attempt, result)
            try:
                with env.cr.savepoint():
                    authorization = activate_caf(env, request, result)
            except (UserError, IntegrityError) as error:
                self._validation_failed(request, attempt, evidence, error)
                return
            evidence.write({"authorization_id": authorization.id})
            received = authorization.folio_end - authorization.folio_start + 1
            state = "success" if received >= request.requested_quantity else "partial"
            self._close(
                request,
                attempt,
                state,
                {"dispatch_started": True},
                {"authorization_id": authorization.id},
            )

    def _validation_failed(self, request, attempt, evidence, error):
        code = "CAF_VALIDATION_FAILED"
        evidence.write(
            {
                "validation_error_code": code,
                "validation_error": validation_failure_text(error),
            }
        )
        message = error_message(request.env, code)
        self._close(
            request,
            attempt,
            "failed",
            {
                "error_code": code,
                "error_message": message,
                "failure_phase": VALIDATION,
                "dispatch_started": True,
            },
        )
        request._notify_operator(code, message)

    @staticmethod
    def _close(request, attempt, state, attempt_values, request_values=None):
        now = fields.Datetime.now()
        attempt.write({**attempt_values, "state": state, "completed_at": now})
        request._write_internal(
            {
                "state": state,
                "completed_at": now,
                "error_code": attempt_values.get("error_code", False),
                "error_message": attempt_values.get("error_message", False),
                "failure_phase": attempt_values.get("failure_phase", False),
                **(request_values or {}),
            }
        )
