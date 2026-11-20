# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
import uuid
from datetime import timedelta

from psycopg2 import IntegrityError

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services.caf_acquisition import (
    CafAcquisition,
    activate_caf,
    create_evidence,
    validation_failure_text,
)
from ..services.caf_errors import (
    POST_DISPATCH,
    PRE_DISPATCH,
    RETRYABLE_CODES,
    SiiCafError,
)
from ..services.caf_messages import error_message
from ..services.sii_caf_client import (
    CAF_DOCUMENT_CODES,
    ENVIRONMENTS,
    MAX_QUANTITY,
    SiiCafClient,
)
from .caf_request_attempt import FAILURE_PHASES

_logger = logging.getLogger(__name__)

REQUEST_STATES = [
    ("pending", "Pending Approval"),
    ("approved", "Approved"),
    ("running", "Running"),
    ("success", "Success"),
    ("partial", "Partial"),
    ("failed", "Failed"),
    ("uncertain", "Uncertain"),
    ("reconciled", "Reconciled"),
    ("cancelled", "Cancelled"),
]
OUTSTANDING_STATES = ("pending", "approved", "running", "uncertain")
# Attempts allowed for one request; only pre-dispatch failures are retried.
MAX_ATTEMPTS = 3
# A running attempt older than this was interrupted (HTTP timeouts are ~70s).
STALE_RUNNING_MINUTES = 30
EDITABLE_FIELDS = {"document_code", "requested_quantity", "environment", "note"}
EDITABLE_INTERNAL_FIELDS = {
    "reconciliation_authorization_id",
    "message_main_attachment_id",
}


class CafRequest(models.Model):
    _name = "l10n_cl.caf.request"
    _description = "SII CAF Request"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"
    _check_company_auto = True
    _sql_constraints = [
        (
            "company_idempotency_key_uniq",
            "unique(company_id, idempotency_key)",
            "This CAF request already exists.",
        ),
    ]

    name = fields.Char(required=True, readonly=True, copy=False, default="/")
    company_id = fields.Many2one(
        "res.company", required=True, index=True, default=lambda self: self.env.company
    )
    environment = fields.Selection(
        list(ENVIRONMENTS),
        string="SII Environment",
        required=True,
        default=lambda self: self._default_environment(),
        tracking=True,
    )
    document_code = fields.Selection(
        list(CAF_DOCUMENT_CODES), string="DTE Type", required=True, tracking=True
    )
    requested_quantity = fields.Integer(required=True, tracking=True)
    note = fields.Text()
    rule_id = fields.Many2one(
        "l10n_cl.caf.replenishment.rule",
        string="Replenishment Rule",
        readonly=True,
        index=True,
        check_company=True,
        ondelete="set null",
        copy=False,
    )
    availability_snapshot = fields.Integer(readonly=True, copy=False)
    idempotency_key = fields.Char(required=True, readonly=True, index=True, copy=False)
    state = fields.Selection(
        REQUEST_STATES,
        default="pending",
        required=True,
        readonly=True,
        index=True,
        copy=False,
        tracking=True,
    )
    requested_by = fields.Many2one(
        "res.users", readonly=True, default=lambda self: self.env.user, copy=False
    )
    approved_by = fields.Many2one("res.users", readonly=True, copy=False)
    approved_at = fields.Datetime(readonly=True, copy=False)
    requested_at = fields.Datetime(string="Last Run", readonly=True, copy=False)
    completed_at = fields.Datetime(readonly=True, copy=False)
    attempt_count = fields.Integer(readonly=True, copy=False)
    attempt_ids = fields.One2many(
        "l10n_cl.caf.request.attempt", "request_id", readonly=True
    )
    evidence_ids = fields.One2many(
        "l10n_cl.caf.request.evidence", "request_id", readonly=True
    )
    error_code = fields.Char(readonly=True, copy=False)
    error_message = fields.Text(readonly=True, copy=False)
    sii_error_code = fields.Char(
        string="SII Assistant Code",
        readonly=True,
        copy=False,
        help="Technical outcome code of the SII web assistant.",
    )
    sii_maximum_quantity = fields.Integer(
        string="SII Maximum Quantity",
        readonly=True,
        copy=False,
        help="Maximum folio quantity the SII authorized for this DTE type.",
    )
    folio_start = fields.Integer(
        string="Reserved From",
        readonly=True,
        copy=False,
        help="First folio the SII assistant reserved right before allocation.",
    )
    folio_end = fields.Integer(string="Reserved To", readonly=True, copy=False)
    recovery_error_code = fields.Char(readonly=True, copy=False)
    failure_phase = fields.Selection(FAILURE_PHASES, readonly=True, copy=False)
    authorization_id = fields.Many2one(
        "l10n_cl.caf.authorization",
        string="CAF",
        readonly=True,
        copy=False,
        check_company=True,
        ondelete="restrict",
    )
    reconciliation_authorization_id = fields.Many2one(
        "l10n_cl.caf.authorization",
        string="CAF for Reconciliation",
        copy=False,
        check_company=True,
        domain="[('company_id', '=', company_id), "
        "('document_code', '=', document_code), ('state', '=', 'validated')]",
        help="A validated CAF obtained independently from the SII for the "
        "folios this request may have allocated.",
    )
    reconciled_by = fields.Many2one("res.users", readonly=True, copy=False)
    reconciled_at = fields.Datetime(readonly=True, copy=False)
    can_retry = fields.Boolean(compute="_compute_can_retry")
    can_reconcile = fields.Boolean(compute="_compute_can_retry")
    can_recover = fields.Boolean(compute="_compute_can_retry")

    @api.model
    def _default_environment(self):
        environment = self.env.company.sudo().dte_sii_environment
        return environment if environment in dict(ENVIRONMENTS) else False

    @api.depends(
        "state",
        "attempt_count",
        "error_code",
        "failure_phase",
        "attempt_ids.dispatch_started",
    )
    def _compute_can_retry(self):
        for request in self:
            dispatched = request._last_attempt().dispatch_started
            request.can_retry = (
                request.state == "failed"
                and not dispatched
                and request.error_code in RETRYABLE_CODES
                and request.attempt_count < MAX_ATTEMPTS
            )
            # A CAF that failed local validation may still hold allocated folios.
            request.can_reconcile = request.state == "uncertain" or (
                request.state == "failed" and request.failure_phase == "validation"
            )
            request.can_recover = bool(
                request.state == "uncertain" and request.folio_start
            )

    @api.constrains("requested_quantity")
    def _check_quantity(self):
        for request in self:
            if not 0 < request.requested_quantity <= MAX_QUANTITY:
                raise ValidationError(
                    self.env._(
                        "The requested quantity must be between 1 and %(maximum)s.",
                        maximum=MAX_QUANTITY,
                    )
                )

    @api.constrains("rule_id", "company_id", "document_code", "environment")
    def _check_rule_context(self):
        for request in self.filtered("rule_id"):
            rule = request.rule_id
            if (rule.company_id, rule.document_code, rule.environment) != (
                request.company_id,
                request.document_code,
                request.environment,
            ):
                raise ValidationError(
                    self.env._("The replenishment rule does not match the request.")
                )

    @api.model_create_multi
    def create(self, vals_list):
        """Create requests idempotently: a repeated key returns the existing one."""
        self._check_manager()
        records = self.browse()
        for vals in vals_list:
            values = {
                key: value
                for key, value in vals.items()
                if key in EDITABLE_FIELDS | {"company_id", "idempotency_key"}
                or self.env.context.get("caf_request_internal")
            }
            values.setdefault("company_id", self.env.company.id)
            values.setdefault("idempotency_key", uuid.uuid4().hex)
            values.update(state=values.get("state", "pending"))
            existing = self._find_by_key(values)
            if existing:
                records |= existing
                continue
            values["name"] = self.env["ir.sequence"].next_by_code(
                "l10n_cl.caf.request"
            ) or self.env._("New")
            try:
                with self.env.cr.savepoint():
                    records |= super().create([values])
            except IntegrityError:
                # A concurrent submission with the same key won the race.
                existing = self._find_by_key(values)
                if not existing:
                    raise
                records |= existing
        return records

    def _find_by_key(self, values):
        return self.search(
            [
                ("company_id", "=", values["company_id"]),
                ("idempotency_key", "=", values["idempotency_key"]),
            ],
            limit=1,
        )

    def write(self, vals):
        self._check_manager()
        protected = set(vals) - EDITABLE_FIELDS - EDITABLE_INTERNAL_FIELDS
        if protected:
            raise UserError(
                self.env._("CAF request status changes only through its actions.")
            )
        if set(vals) & EDITABLE_FIELDS and any(
            request.state != "pending" for request in self
        ):
            raise UserError(self.env._("Only a pending CAF request can be edited."))
        return super().write(vals)

    def _write_internal(self, values):
        """Persist lifecycle fields from the validated actions of this addon."""
        return super().write(values)

    def _check_manager(self):
        if not self.env.su and not self.env.user.has_group(
            "l10n_cl_dte_core.group_dte_manager"
        ):
            raise AccessError(
                self.env._("Only a DTE manager can create or run CAF requests.")
            )

    def _last_attempt(self):
        self.ensure_one()
        return self.attempt_ids.sorted("number", reverse=True)[:1]

    def action_approve(self):
        self._check_manager()
        for request in self:
            if request.state != "pending":
                raise UserError(
                    self.env._("Only a pending CAF request can be approved.")
                )
            request._write_internal(
                {
                    "state": "approved",
                    "approved_by": self.env.uid,
                    "approved_at": fields.Datetime.now(),
                }
            )
        return True

    def action_cancel(self):
        self._check_manager()
        for request in self:
            if request.state not in ("pending", "approved", "failed"):
                raise UserError(
                    self.env._("This CAF request cannot be cancelled in its state.")
                )
            request._write_internal({"state": "cancelled"})
        return True

    def action_execute(self):
        """Run an approved request now, on behalf of the current DTE manager."""
        self.ensure_one()
        self._check_manager()
        return self._run(("approved",))

    def action_retry(self):
        """Retry a pre-dispatch failure within the bounded safe-retry policy."""
        self.ensure_one()
        self._check_manager()
        return self._run(("failed",), self._check_safe_retry)

    def _check_safe_retry(self, request):
        attempt = request._last_attempt()
        if not attempt or attempt.dispatch_started:
            raise UserError(
                request.env._(
                    "The CAF request may have reached the SII; it cannot be "
                    "repeated. Reconcile it with a CAF obtained from the SII."
                )
            )
        if request.error_code not in RETRYABLE_CODES:
            raise UserError(
                request.env._("Repeating this CAF request would not change its result.")
            )
        if request.attempt_count >= MAX_ATTEMPTS:
            raise UserError(request.env._("The safe retry limit has been reached."))

    def _run(self, allowed_states, check=None):
        CafAcquisition(self, self._get_caf_client()).run(allowed_states, check)
        return True

    def _get_caf_client(self):
        """Return the SII client of this request's explicit environment."""
        self.ensure_one()
        return SiiCafClient(self.environment)

    def action_reconcile(self):
        """Close an uncertain request with a CAF obtained independently."""
        self.ensure_one()
        self._check_manager()
        authorization = self.reconciliation_authorization_id
        if not self.can_reconcile:
            raise UserError(
                self.env._(
                    "Only an uncertain request, or one whose returned CAF failed "
                    "validation, can be reconciled."
                )
            )
        if not authorization:
            raise UserError(self.env._("Select the CAF that reconciles this request."))
        if (
            authorization.company_id != self.company_id
            or authorization.document_code != self.document_code
            or authorization.sudo().sii_environment != self.environment
            or authorization.state != "validated"
        ):
            raise ValidationError(
                self.env._(
                    "The reconciliation CAF must be a validated CAF of the same "
                    "company, DTE type and SII environment."
                )
            )
        self._reconcile(authorization)
        return True

    def _reconcile(self, authorization):
        self._write_internal(
            {
                "state": "reconciled",
                "authorization_id": authorization.id,
                "recovery_error_code": False,
                "reconciled_by": self.env.uid,
                "reconciled_at": fields.Datetime.now(),
            }
        )
        self.message_post(
            body=self.env._("Reconciled with CAF %(caf)s.", caf=authorization.name)
        )

    def action_recover_from_sii(self):
        """Download the CAF of the reserved range of an uncertain request.

        The SII recovery pages only re-download already authorized ranges, so
        this never allocates folios. On success the CAF is validated and the
        request is reconciled; otherwise it stays uncertain.
        """
        self.ensure_one()
        self._check_manager()
        if not self.can_recover:
            raise UserError(
                self.env._(
                    "Only an uncertain request with a reserved folio range can be "
                    "recovered from the SII."
                )
            )
        authorization = self._find_range_authorization()
        if not authorization:
            authorization = self._recover_authorization()
        if authorization:
            self._reconcile(authorization)
        return True

    def _find_range_authorization(self):
        return self.env["l10n_cl.caf.authorization"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("sii_environment", "=", self.environment),
                ("document_code", "=", self.document_code),
                ("folio_start", "=", self.folio_start),
                ("folio_end", "=", self.folio_end),
                ("state", "in", ["validated", "exhausted"]),
            ],
            limit=1,
        )

    def _recover_authorization(self):
        authorization = self.env["l10n_cl.caf.authorization"]
        try:
            result = self._get_caf_client().recover_caf(
                self.company_id, self.document_code, self.folio_start, self.folio_end
            )
        except SiiCafError as error:
            code = error.sii_code or error.code
            self._write_internal({"recovery_error_code": code})
            self._notify_operator(code, error_message(self.env, error.code))
            return authorization
        evidence = create_evidence(self.env, self, self._last_attempt(), result)
        try:
            with self.env.cr.savepoint():
                authorization = activate_caf(self.env, self, result)
        except (UserError, IntegrityError) as error:
            evidence.write(
                {
                    "validation_error_code": "CAF_VALIDATION_FAILED",
                    "validation_error": validation_failure_text(error),
                }
            )
            self._write_internal({"recovery_error_code": "CAF_VALIDATION_FAILED"})
            message = error_message(self.env, "CAF_VALIDATION_FAILED")
            self._notify_operator("CAF_VALIDATION_FAILED", message)
            return authorization
        evidence.write({"authorization_id": authorization.id})
        return authorization

    def _notify_operator(self, code, message):
        """Post the fixed message and plan a review activity for the operator."""
        self.ensure_one()
        user = self.approved_by or self.requested_by
        try:
            with self.env.cr.savepoint():
                self.sudo().message_post(body=f"{code}: {message}")
                if user:
                    self.sudo().activity_schedule(
                        "mail.mail_activity_data_todo",
                        user_id=user.id,
                        summary=self.env._(
                            "Review CAF request %(name)s", name=self.name
                        ),
                        note=message,
                    )
        except (UserError, ValueError) as error:
            # Notification never replaces the persisted acquisition result.
            _logger.warning(
                "CAF request %s notification failed (%s)",
                self.id,
                type(error).__name__,
            )

    @api.model
    def _cron_process_requests(self, limit=20):
        """Scheduled job: the only place where requests run without an operator.

        Only requests queued by an active rule with explicit automatic
        execution are run, as the rule's execution user. Uncertain requests are
        never retried.
        """
        stale = self._recover_stale_running()
        limit = min(max(int(limit or 1), 1), 100)
        requests = self.sudo().search(
            [
                ("state", "in", ("approved", "failed")),
                ("rule_id.active", "=", True),
                ("rule_id.approval_mode", "=", "automatic"),
                ("rule_id.execution_user_id", "!=", False),
                # Recovered rows stay locked by this transaction until it ends.
                ("id", "not in", stale.ids),
            ],
            order="id",
            limit=limit,
        )
        for request in requests:
            user = request.rule_id.execution_user_id
            request = request.with_user(user).with_company(request.company_id)
            try:
                if request.state == "approved":
                    request._run(("approved",))
                elif request.can_retry:
                    request._run(("failed",), request._check_safe_retry)
            except (UserError, AccessError) as error:
                _logger.warning(
                    "Scheduled CAF request %s was not run (%s)",
                    request.id,
                    type(error).__name__,
                )
        return requests

    @api.model
    def _recover_stale_running(self):
        """Close attempts interrupted by a crash without ever re-sending them."""
        cutoff = fields.Datetime.now() - timedelta(minutes=STALE_RUNNING_MINUTES)
        stale = self.sudo().search(
            [("state", "=", "running"), ("requested_at", "<", cutoff)]
        )
        for request in stale:
            attempt = request._last_attempt()
            if attempt.dispatch_started:
                state, code, phase = (
                    "uncertain",
                    "SII_DISPATCH_UNCERTAIN",
                    POST_DISPATCH,
                )
            else:
                state, code, phase = (
                    "failed",
                    "INTERRUPTED_BEFORE_DISPATCH",
                    PRE_DISPATCH,
                )
            message = error_message(self.env, code)
            now = fields.Datetime.now()
            values = {"error_code": code, "error_message": message}
            attempt.write(
                {**values, "state": state, "failure_phase": phase, "completed_at": now}
            )
            request._write_internal(
                {**values, "state": state, "failure_phase": phase, "completed_at": now}
            )
            request._notify_operator(code, message)
        return stale
