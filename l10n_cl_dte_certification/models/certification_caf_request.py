# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Durable CAF acquisition requests and per-document outcomes."""

import logging
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)


class DteCertificationCafRequest(models.Model):
    _name = "l10n_cl.dte.certification.caf.request"
    _description = "Certification CAF Request"
    _order = "create_date desc, id desc"
    _check_company_auto = True
    _sql_constraints = [
        (
            "idempotency_key_uniq",
            "unique(idempotency_key)",
            "A CAF request idempotency key must be unique.",
        ),
    ]

    name = fields.Char(required=True, readonly=True, copy=False, index=True)
    set_id = fields.Many2one(
        "l10n_cl.dte.certification.set",
        required=True,
        index=True,
        check_company=True,
        ondelete="restrict",
        readonly=True,
    )
    company_id = fields.Many2one(
        "res.company", required=True, index=True, readonly=True
    )
    environment = fields.Selection(
        [("certification", "Certification")],
        required=True,
        default="certification",
        readonly=True,
    )
    state = fields.Selection(
        [
            ("queued", "Queued"),
            ("running", "Running"),
            ("success", "Success"),
            ("partial", "Partial"),
            ("failed", "Failed"),
            ("uncertain", "Uncertain"),
            ("reconciled", "Reconciled"),
            ("cancelled", "Cancelled"),
        ],
        required=True,
        default="queued",
        readonly=True,
        index=True,
    )
    requested_by = fields.Many2one(
        "res.users", required=True, readonly=True, copy=False
    )
    requested_at = fields.Datetime(required=True, readonly=True, copy=False)
    started_at = fields.Datetime(readonly=True, copy=False)
    completed_at = fields.Datetime(readonly=True, copy=False)
    idempotency_key = fields.Char(required=True, readonly=True, copy=False, index=True)
    line_ids = fields.One2many(
        "l10n_cl.dte.certification.caf.request.line", "request_id", copy=False
    )
    has_queued_lines = fields.Boolean(compute="_compute_has_queued_lines")
    diagnostic_code = fields.Char(readonly=True, copy=False)
    diagnostic_message = fields.Char(readonly=True, copy=False, size=500)

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super
        raise AccessError(_("Create CAF requests using the request wizard."))

    def write(self, vals):  # pylint: disable=method-required-super
        raise AccessError(_("CAF request outcomes are managed by the service."))

    def unlink(self):  # pylint: disable=method-required-super
        raise AccessError(_("CAF request audit records cannot be deleted."))

    @api.depends("line_ids.state")
    def _compute_has_queued_lines(self):
        for request in self:
            request.has_queued_lines = any(
                line.state == "queued" for line in request.line_ids
            )

    @api.constrains("set_id", "company_id", "environment")
    def _check_request_identity(self):
        for request in self:
            if (
                request.company_id != request.set_id.company_id
                or request.environment != "certification"
                or request.set_id.sii_environment != "certification"
            ):
                raise ValidationError(
                    _("A CAF request must belong to a Certification set and company.")
                )

    @api.model
    def _create_from_wizard(self, certification_set, quantities):
        """Create a confirmed, queued request with explicit type quantities."""
        certification_set.ensure_one()
        certification_set._check_certification_access()
        certification_set._lock_configuration()
        certification_set._check_no_active_caf_requests()
        if (
            not certification_set.can_edit_envelope
            or certification_set.company_id.dte_sii_environment != "certification"
            or not certification_set.company_id.dte_certification_enabled
        ):
            raise UserError(_("CAF requests require an editable Certification set."))
        if not quantities:
            raise ValidationError(_("Add at least one document type and quantity."))
        allowed_codes = {case.document_code for case in certification_set.case_ids}
        request = super().create(
            {
                "name": self.env["ir.sequence"].next_by_code(
                    "l10n_cl.dte.certification.caf.request"
                ),
                "set_id": certification_set.id,
                "company_id": certification_set.company_id.id,
                "environment": "certification",
                "state": "queued",
                "requested_by": self.env.uid,
                "requested_at": fields.Datetime.now(),
                "idempotency_key": str(uuid.uuid4()),
            }
        )
        lines = []
        seen_codes = set()
        for document_type, quantity in quantities:
            code = str(document_type.code)
            if code not in allowed_codes:
                raise ValidationError(
                    _("Document type %(code)s is not present in this set.")
                    % {"code": code}
                )
            if code in seen_codes:
                raise ValidationError(
                    _("A document type can only be requested once per batch.")
                )
            if isinstance(quantity, bool) or int(quantity) < 1:
                raise ValidationError(_("Requested quantities must be positive."))
            seen_codes.add(code)
            if document_type.country_id != certification_set.company_id.country_id:
                raise ValidationError(
                    _("Document type %(code)s must belong to the set country.")
                    % {"code": code}
                )
            lines.append(
                {
                    "request_id": request.id,
                    "document_type_id": document_type.id,
                    "requested_quantity": int(quantity),
                }
            )
        self.env["l10n_cl.dte.certification.caf.request.line"]._create_internal(lines)
        return request

    def _write_internal(self, vals):
        return super().write(vals)

    def action_process(self):
        """Start pre-dispatch preparation; the POST runs after this commits."""
        self.ensure_one()
        self._check_request_access()
        if self.state != "queued":
            raise UserError(_("Only queued CAF requests can be processed."))
        from ..services.certification_caf_service import CertificationCafService

        CertificationCafService(self.env).start_request(self)
        return True

    def action_recover_cafs(self):
        self.ensure_one()
        self._check_request_access()
        lines = self.line_ids.filtered(lambda line: line.state == "uncertain")
        if not lines:
            raise UserError(_("This request has no uncertain CAFs to recover."))
        for line in lines:
            line.action_recover_caf()
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_cancel(self):
        """Cancel a request only while no folio-generation POST was prepared."""
        self.ensure_one()
        self._check_request_access()
        queued_lines = self.line_ids.filtered(lambda line: line.state == "queued")
        if not queued_lines:
            raise UserError(_("This CAF request has no queued lines to cancel."))
        queued_lines._write_internal(
            {"state": "cancelled", "completed_at": fields.Datetime.now()}
        )
        if self.state == "queued" and len(queued_lines) == len(self.line_ids):
            self._write_internal(
                {"state": "cancelled", "completed_at": fields.Datetime.now()}
            )
        return True

    def action_open(self):
        self.ensure_one()
        self._check_request_access()
        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def _check_request_access(self):
        for request in self:
            if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
                raise AccessError(_("Only a DTE manager can manage CAF requests."))
            request.set_id._check_certification_access()
            if (
                request.environment != "certification"
                or request.company_id != request.set_id.company_id
                or request.company_id.dte_sii_environment != "certification"
                or not request.company_id.dte_certification_enabled
            ):
                raise UserError(_("CAF requests are restricted to Certification."))

    @api.model
    def _cron_process_pending(self):
        """Claim queued requests and prepare one durable dispatch at a time."""
        self.env.cr.execute(
            """SELECT id FROM l10n_cl_dte_certification_caf_request
                 WHERE state = 'queued'
                 ORDER BY requested_at, id
                 FOR UPDATE SKIP LOCKED
                 LIMIT 5"""
        )
        for (request_id,) in self.env.cr.fetchall():
            request = self.browse(request_id)
            request._write_internal(
                {"state": "running", "started_at": fields.Datetime.now()}
            )
            from ..services.certification_caf_service import CertificationCafService

            CertificationCafService(self.env).start_request(
                request, already_claimed=True
            )

    @api.model
    def _cron_recover_dispatching(self):
        """Close stale dispatches as uncertain without sending them again."""
        self.env.cr.execute(
            """SELECT id FROM l10n_cl_dte_certification_caf_request_line
                 WHERE state = 'dispatching'
                   AND started_at < NOW() - INTERVAL '15 minutes'
                 FOR UPDATE SKIP LOCKED"""
        )
        line_ids = [row[0] for row in self.env.cr.fetchall()]
        lines = self.env["l10n_cl.dte.certification.caf.request.line"].browse(line_ids)
        for line in lines:
            line._write_internal(
                {
                    "state": "uncertain",
                    "error_code": "worker_lost_after_dispatch_marker",
                    "error_message": _(
                        "The worker stopped after dispatch was prepared. Reconcile "
                        "this range with Folios ya autorizados; do not request it "
                        "again."
                    ),
                    "completed_at": fields.Datetime.now(),
                }
            )
            line.request_id._write_internal(
                {
                    "state": "uncertain",
                    "completed_at": fields.Datetime.now(),
                    "diagnostic_code": "dispatch_outcome_unknown",
                    "diagnostic_message": _(
                        "At least one dispatch outcome is unknown. Reconcile it "
                        "with the SII portal before continuing."
                    ),
                }
            )


class DteCertificationCafRequestLine(models.Model):
    _name = "l10n_cl.dte.certification.caf.request.line"
    _description = "Certification CAF Request Line"
    _order = "id"
    _check_company_auto = True
    _sql_constraints = [
        (
            "request_document_type_uniq",
            "unique(request_id, document_type_id)",
            "A request can contain a document type only once.",
        ),
        (
            "requested_quantity_positive",
            "CHECK(requested_quantity > 0)",
            "Requested quantities must be positive.",
        ),
        (
            "folio_range_valid",
            "CHECK(folio_start IS NULL OR folio_end >= folio_start)",
            "The expected folio range is invalid.",
        ),
    ]

    request_id = fields.Many2one(
        "l10n_cl.dte.certification.caf.request",
        required=True,
        index=True,
        ondelete="cascade",
        check_company=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        related="request_id.company_id", store=True, index=True
    )
    document_type_id = fields.Many2one(
        "l10n_latam.document.type", required=True, ondelete="restrict", readonly=True
    )
    document_code = fields.Char(
        related="document_type_id.code", store=True, readonly=True, index=True
    )
    requested_quantity = fields.Integer(required=True, readonly=True)
    state = fields.Selection(
        [
            ("queued", "Queued"),
            ("dispatching", "Dispatching"),
            ("success", "Success"),
            ("failed", "Failed"),
            ("uncertain", "Uncertain"),
            ("reconciled", "Reconciled"),
            ("cancelled", "Cancelled"),
        ],
        required=True,
        default="queued",
        readonly=True,
        index=True,
    )
    folio_start = fields.Integer(readonly=True, copy=False)
    folio_end = fields.Integer(readonly=True, copy=False)
    caf_id = fields.Many2one(
        "l10n_cl.caf.authorization", readonly=True, ondelete="restrict", copy=False
    )
    caf_digest = fields.Char(readonly=True, copy=False)
    started_at = fields.Datetime(readonly=True, copy=False)
    completed_at = fields.Datetime(readonly=True, copy=False)
    attempt_count = fields.Integer(default=0, readonly=True, copy=False)
    error_code = fields.Char(readonly=True, copy=False)
    error_message = fields.Char(readonly=True, copy=False, size=500)
    reconciled_by = fields.Many2one("res.users", readonly=True, copy=False)
    reconciled_at = fields.Datetime(readonly=True, copy=False)
    reconciliation_note = fields.Char(readonly=True, copy=False, size=500)
    recovery_attempt_count = fields.Integer(readonly=True, copy=False)
    last_recovery_at = fields.Datetime(readonly=True, copy=False)
    recovery_error_code = fields.Char(readonly=True, copy=False)
    recovery_message = fields.Char(readonly=True, copy=False, size=500)
    sii_maximum_known = fields.Boolean(readonly=True, copy=False)
    sii_maximum_quantity = fields.Integer(
        string="SII Maximum", readonly=True, copy=False
    )

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super
        raise AccessError(_("Create CAF request lines through a request wizard."))

    def write(self, vals):  # pylint: disable=method-required-super
        raise AccessError(_("CAF request line outcomes are managed by the service."))

    def unlink(self):  # pylint: disable=method-required-super
        raise AccessError(_("CAF request audit lines cannot be deleted."))

    @api.model
    def _create_internal(self, vals_list):
        for vals in vals_list:
            if vals.get("requested_quantity", 0) < 1:
                raise ValidationError(_("Requested quantities must be positive."))
        return super().create(vals_list)

    def _write_internal(self, vals):
        return super().write(vals)

    @api.constrains("request_id", "document_type_id", "requested_quantity")
    def _check_request_line(self):
        for line in self:
            case_codes = set(line.request_id.set_id.case_ids.mapped("document_code"))
            if line.requested_quantity < 1:
                raise ValidationError(_("Requested quantities must be positive."))
            if (
                line.document_code not in {"33", "34", "56", "61"}
                or line.document_code not in case_codes
                or line.document_type_id.country_id
                != line.request_id.company_id.country_id
            ):
                raise ValidationError(
                    _("The requested CAF type must be supported by this set.")
                )

    def action_open_reconcile_wizard(self):
        self.ensure_one()
        self.request_id._check_request_access()
        if self.state != "uncertain":
            raise UserError(_("Only uncertain CAF requests can be reconciled."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Reconcile Authorized Folios"),
            "res_model": "l10n_cl.dte.certification.caf.reconcile.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_line_id": self.id},
        }

    def action_recover_caf(self):
        from ..services.certification_caf_service import CertificationCafService

        self.ensure_one()
        CertificationCafService(self.env).recover_line(self)
        return {"type": "ir.actions.client", "tag": "reload"}
