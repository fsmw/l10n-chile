# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services.rcv_move_check import RcvMoveCheck
from ..services.sii_rcv_client import RECEIPT_READ_UNSUPPORTED, SII_OPERATIONS

RCV_OPERATIONS = [
    ("purchase", "Purchases"),
    ("sale", "Sales"),
    ("boleta", "Receipt Sales"),
]
DOCUMENT_OPERATIONS = [("purchase", "Purchases"), ("sale", "Sales")]
RCV_SOURCES = [("sii", "SII"), ("csv", "CSV")]
MANAGER_GROUP = "l10n_cl_dte_core.group_dte_manager"
ACTIVE_READ_STATES = ("queued", "running")
MIN_YEAR = 2000
MAX_YEAR = 9999


class RcvPeriod(models.Model):
    _name = "l10n_cl.dte.rcv.period"
    _description = "SII RCV Period"
    _order = "year desc, month desc, operation"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        ondelete="cascade",
    )
    year = fields.Integer(required=True, index=True)
    month = fields.Integer(required=True)
    tax_period = fields.Char(
        compute="_compute_name",
        store=True,
        index=True,
        help="Tax period as YYYYMM, as the SII reports it.",
    )
    operation = fields.Selection(RCV_OPERATIONS, required=True, index=True)
    state = fields.Selection(
        [
            ("not_synced", "Not Read"),
            ("pending", "Pending"),
            ("partial", "Partial"),
            ("success", "Complete"),
            ("failed", "Failed"),
        ],
        default="not_synced",
        required=True,
        index=True,
    )
    last_sync_at = fields.Datetime(string="Last Read", readonly=True)
    current_sync_id = fields.Many2one(
        "l10n_cl.dte.rcv.sync",
        string="Current Read",
        readonly=True,
        ondelete="set null",
        check_company=True,
    )
    summary_ids = fields.One2many(
        related="current_sync_id.summary_ids", string="Current Summary"
    )
    sync_ids = fields.One2many("l10n_cl.dte.rcv.sync", "period_id", string="Reads")
    document_count = fields.Integer(compute="_compute_counts")
    summary_count = fields.Integer(compute="_compute_counts")
    currency_id = fields.Many2one(related="company_id.currency_id", store=True)

    _sql_constraints = [
        (
            "period_company_month_operation_uniq",
            "unique(company_id, year, month, operation)",
            "This RCV period already exists for the company.",
        ),
        (
            "period_month_valid",
            "check(month >= 1 and month <= 12)",
            "The month must be between 1 and 12.",
        ),
    ]

    @api.constrains("year")
    def _check_year(self):
        for period in self:
            if not MIN_YEAR <= period.year <= MAX_YEAR:
                raise ValidationError(
                    self.env._(
                        "The RCV year must be between %(min)s and %(max)s.",
                        min=MIN_YEAR,
                        max=MAX_YEAR,
                    )
                )

    @api.depends("year", "month", "operation")
    def _compute_name(self):
        labels = dict(self._fields["operation"]._description_selection(self.env))
        for period in self:
            period.tax_period = f"{period.year:04d}{period.month:02d}"
            period.name = "{:04d}-{:02d} · {}".format(
                period.year, period.month, labels.get(period.operation, "")
            )

    def _document_domain(self):
        self.ensure_one()
        return [
            ("company_id", "=", self.company_id.id),
            ("operation", "=", self.operation),
            ("tax_period", "=", self.tax_period),
        ]

    def _compute_counts(self):
        document_model = self.env["l10n_cl.dte.rcv.document"]
        for period in self:
            period.document_count = (
                document_model.search_count(period._document_domain())
                if period.id
                else 0
            )
            period.summary_count = len(period.current_sync_id.summary_ids)

    def action_open_documents(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "l10n_cl_dte_rcv.action_rcv_document"
        )
        action["domain"] = self._document_domain()
        return action

    def action_reconcile(self):
        """Reconcile the documents of the periods; DTE managers only."""
        document_model = self.env["l10n_cl.dte.rcv.document"]
        documents = document_model.browse()
        for period in self:
            documents |= document_model.search(period._document_domain())
        action = documents.action_reconcile()
        for period in self.filtered("current_sync_id"):
            RcvMoveCheck(self.env).check(period.current_sync_id)
        return action

    def action_sync(self):
        """Queue an SII read of each period; only DTE managers can start it."""
        self._check_sii_read_allowed()
        for period in self:
            period._queue_sii_read(self.env.user)
        self.env["l10n_cl.dte.rcv.sync"]._trigger_processing()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "info",
                "message": self.env._(
                    "The SII read was queued. Its result is shown on the period "
                    "once it finishes."
                ),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def _check_sii_read_allowed(self):
        if not self.env.user.has_group(MANAGER_GROUP):
            raise AccessError(
                self.env._("Only a DTE manager can read the RCV from the SII.")
            )
        if self.company_id - self.env.companies:
            raise UserError(
                self.env._("Select one of the companies you are working in.")
            )
        if any(period.operation not in SII_OPERATIONS for period in self):
            raise UserError(str(RECEIPT_READ_UNSUPPORTED))

    def _active_sii_read(self):
        self.ensure_one()
        return self.env["l10n_cl.dte.rcv.sync"].search(
            [
                ("period_id", "=", self.id),
                ("source", "=", "sii"),
                ("state", "in", ACTIVE_READ_STATES),
            ],
            limit=1,
        )

    def _queue_sii_read(self, user, scheduled_for=False):
        """Return the queued or running SII read of the period, or queue one."""
        self.ensure_one()
        active = self._active_sii_read()
        if active:
            return active
        sync = self.env["l10n_cl.dte.rcv.sync"].create(
            {
                "name": self.env._("SII %(period)s", period=self.name),
                "period_id": self.id,
                "company_id": self.company_id.id,
                "operation": self.operation,
                "requested_by": user.id,
                "source": "sii",
                "scheduled_for": scheduled_for,
            }
        )
        if self.state in ("not_synced", "failed"):
            self.state = "pending"
        return sync

    @api.model
    def _ensure_year(self, year, company):
        """Create the missing periods of ``year`` for ``company``.

        Period records are empty shells; reading or importing them is still
        restricted to RCV managers.
        """
        periods = self.sudo().with_company(company)
        existing = periods.search(
            [("company_id", "=", company.id), ("year", "=", year)]
        )
        keys = {(period.month, period.operation) for period in existing}
        missing = [
            {
                "company_id": company.id,
                "year": year,
                "month": month,
                "operation": operation,
            }
            for month in range(1, 13)
            for operation, _label in RCV_OPERATIONS
            if (month, operation) not in keys
        ]
        return existing | periods.create(missing)

    @api.model
    def action_open_year(self, year=None):
        """Open the periods of ``year`` (default: current) for the company."""
        year = int(year or fields.Date.context_today(self).year)
        company = self.env.company
        self._ensure_year(year, company)
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "l10n_cl_dte_rcv.action_rcv_period"
        )
        action["domain"] = [("company_id", "=", company.id), ("year", "=", year)]
        action["context"] = {"search_default_group_month": 1}
        return action
