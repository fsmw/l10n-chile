# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from ..services.caf_availability import available_folios
from ..services.sii_caf_client import CAF_DOCUMENT_CODES, ENVIRONMENTS, MAX_QUANTITY
from .caf_request import OUTSTANDING_STATES

_logger = logging.getLogger(__name__)

SCAN_STATES = [
    ("above_threshold", "Above Threshold"),
    ("outstanding", "Outstanding Request"),
    ("stopped", "Stopped After Failure"),
    ("cooldown", "Cooldown"),
    ("queued", "Request Queued"),
    ("error", "Evaluation Error"),
]


class CafReplenishmentRule(models.Model):
    _name = "l10n_cl.caf.replenishment.rule"
    _description = "CAF Replenishment Rule"
    _order = "company_id, document_code"
    _check_company_auto = True
    _sql_constraints = [
        (
            "company_document_environment_uniq",
            "unique(company_id, document_code, environment)",
            "Only one replenishment rule is allowed per company, DTE type and "
            "SII environment.",
        ),
    ]

    company_id = fields.Many2one(
        "res.company", required=True, index=True, default=lambda self: self.env.company
    )
    document_code = fields.Selection(
        list(CAF_DOCUMENT_CODES), string="DTE Type", required=True
    )
    environment = fields.Selection(
        list(ENVIRONMENTS),
        string="SII Environment",
        required=True,
        default=lambda self: self.env["l10n_cl.caf.request"]._default_environment(),
    )
    active = fields.Boolean(default=True)
    threshold = fields.Integer(
        string="Low Availability Threshold",
        default=10,
        required=True,
        help="Queue a request when available folios are at or below this value.",
    )
    request_quantity = fields.Integer(default=50, required=True)
    approval_mode = fields.Selection(
        [
            ("operator", "Operator Approval"),
            ("automatic", "Automatic Execution by the Scheduled Job"),
        ],
        default="operator",
        required=True,
        help="Automatic execution is an explicit opt-in: the scheduled job then "
        "requests the CAF from the SII without an operator.",
    )
    execution_user_id = fields.Many2one(
        "res.users",
        string="Execute As",
        help="DTE manager whose signing identity the scheduled job uses for "
        "automatic execution.",
    )
    cooldown_hours = fields.Integer(default=24, required=True)
    max_outstanding = fields.Integer(
        string="Maximum Outstanding Requests", default=1, required=True
    )
    available_folios = fields.Integer(compute="_compute_available_folios")
    last_scan_at = fields.Datetime(readonly=True, copy=False)
    last_scan_available = fields.Integer(readonly=True, copy=False)
    last_scan_state = fields.Selection(SCAN_STATES, readonly=True, copy=False)
    last_request_id = fields.Many2one(
        "l10n_cl.caf.request", readonly=True, copy=False, check_company=True
    )
    request_ids = fields.One2many("l10n_cl.caf.request", "rule_id", readonly=True)

    @api.depends("company_id", "document_code", "environment")
    def _compute_display_name(self):
        codes = dict(self._fields["document_code"]._description_selection(self.env))
        for rule in self:
            rule.display_name = (
                f"{codes.get(rule.document_code, '')} ({rule.company_id.name})"
            )

    @api.depends("company_id", "document_code", "environment")
    def _compute_available_folios(self):
        for rule in self:
            rule.available_folios = (
                available_folios(
                    self.env, rule.company_id, rule.document_code, rule.environment
                )
                if rule.company_id and rule.document_code and rule.environment
                else 0
            )

    @api.constrains(
        "threshold", "request_quantity", "cooldown_hours", "max_outstanding"
    )
    def _check_values(self):
        for rule in self:
            if rule.threshold < 0 or rule.cooldown_hours < 0:
                raise ValidationError(
                    self.env._("Threshold and cooldown cannot be negative.")
                )
            if (
                not 0 < rule.request_quantity <= MAX_QUANTITY
                or rule.max_outstanding < 1
            ):
                raise ValidationError(
                    self.env._(
                        "The request quantity must be between 1 and %(maximum)s and "
                        "at least one outstanding request must be allowed.",
                        maximum=MAX_QUANTITY,
                    )
                )

    @api.constrains("approval_mode", "execution_user_id", "company_id")
    def _check_execution_user(self):
        for rule in self.filtered(lambda item: item.approval_mode == "automatic"):
            user = rule.execution_user_id
            if (
                not user
                or not user.has_group("l10n_cl_dte_core.group_dte_manager")
                or rule.company_id not in user.company_ids
            ):
                raise ValidationError(
                    self.env._(
                        "Automatic execution requires a DTE manager of the rule's "
                        "company as execution user."
                    )
                )

    def _check_manager(self):
        if not self.env.su and not self.env.user.has_group(
            "l10n_cl_dte_core.group_dte_manager"
        ):
            raise AccessError(
                self.env._("Only a DTE manager can evaluate replenishment rules.")
            )

    def action_scan(self):
        """Evaluate the rules now; this only queues requests, it never runs one."""
        self._check_manager()
        for rule in self:
            rule.sudo()._scan_one()
        return True

    def _record_scan(self, state, available, request=False):
        self.ensure_one()
        self.sudo().write(
            {
                "last_scan_at": fields.Datetime.now(),
                "last_scan_available": available,
                "last_scan_state": state,
                "last_request_id": request.id if request else False,
            }
        )

    def _scan_one(self):
        """Queue at most one request when availability is low; no SII call."""
        self.ensure_one()
        requests = self.env["l10n_cl.caf.request"].sudo()
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [f"l10n_cl_caf_request:{self.company_id.id}:{self.document_code}"],
        )
        if self.company_id.sudo().dte_sii_environment != self.environment:
            self._record_scan("error", 0)
            return requests
        available = available_folios(
            self.env, self.company_id, self.document_code, self.environment
        )
        if available > self.threshold:
            self._record_scan("above_threshold", available)
            return requests
        domain = [
            ("company_id", "=", self.company_id.id),
            ("document_code", "=", self.document_code),
            ("environment", "=", self.environment),
        ]
        outstanding = requests.search(domain + [("state", "in", OUTSTANDING_STATES)])
        if len(outstanding) >= self.max_outstanding:
            self._record_scan("outstanding", available, outstanding[:1])
            return requests
        latest = requests.search(domain, order="id desc", limit=1)
        if latest.state == "failed" and self.approval_mode == "automatic":
            # The scheduled job retries pre-dispatch failures; nothing else.
            self._record_scan("stopped", available, latest)
            return requests
        cutoff = fields.Datetime.now() - timedelta(hours=self.cooldown_hours)
        if latest and self.cooldown_hours and latest.create_date >= cutoff:
            self._record_scan("cooldown", available, latest)
            return requests
        request = self._queue_request(available, latest)
        self._record_scan("queued", available, request)
        return request

    def _queue_request(self, available, latest):
        automatic = self.approval_mode == "automatic"
        return (
            self.env["l10n_cl.caf.request"]
            .sudo()
            .with_context(caf_request_internal=True)
            .create(
                [
                    {
                        "company_id": self.company_id.id,
                        "document_code": self.document_code,
                        "environment": self.environment,
                        "requested_quantity": self.request_quantity,
                        "rule_id": self.id,
                        "availability_snapshot": available,
                        "state": "approved" if automatic else "pending",
                        "approved_by": self.execution_user_id.id
                        if automatic
                        else False,
                        "approved_at": fields.Datetime.now() if automatic else False,
                        "requested_by": self.execution_user_id.id or self.env.uid,
                        # Concurrent scans after the same latest request collide.
                        "idempotency_key": f"rule:{self.id}:{latest.id or 0}",
                    }
                ]
            )
        )

    @api.model
    def _cron_scan_rules(self, limit=100):
        """Scheduled job: evaluate active rules; it makes no SII call."""
        limit = min(max(int(limit or 1), 1), 500)
        created = self.env["l10n_cl.caf.request"].sudo()
        for rule in self.sudo().search([], order="id", limit=limit):
            try:
                with self.env.cr.savepoint():
                    created |= rule._scan_one()
            except (ValidationError, AccessError) as error:
                _logger.warning(
                    "CAF replenishment rule %s evaluation failed (%s)",
                    rule.id,
                    type(error).__name__,
                )
                rule._record_scan("error", 0)
        return created
