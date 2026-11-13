# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Durable persistence of each CAF dispatch outcome."""

import base64
import hashlib
import logging

from odoo import SUPERUSER_ID, api, fields

from .certification_caf_diagnostics import _diagnostic, _safe_code

_logger = logging.getLogger(__name__)


def record_quantity_limit(line, maximum):
    """Store the SII-authorized maximum so the operator can retry smaller."""
    if maximum is not None:
        line._write_internal(
            {
                "sii_maximum_known": True,
                "sii_maximum_quantity": maximum,
            }
        )


class CafBatchPersistenceMixin:
    """Record a dispatched CAF line's outcome in its own committed cursor."""

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
            record_quantity_limit(line, error.maximum_quantity)
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
