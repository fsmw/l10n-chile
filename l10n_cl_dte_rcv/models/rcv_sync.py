# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

import psycopg2

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services.rcv_normalizer import RcvNormalizer
from ..services.sii_rcv_client import SII_OPERATIONS, SiiRcvClient
from .rcv_period import RCV_OPERATIONS, RCV_SOURCES

_logger = logging.getLogger(__name__)
ACTIVE_STATES = ("queued", "running")
MAX_ERROR_LENGTH = 500
SII_TIMEZONE = "America/Santiago"


class RcvSync(models.Model):
    """One read of an RCV period, from the SII or from a CSV file."""

    _name = "l10n_cl.dte.rcv.sync"
    _description = "SII RCV Read"
    _order = "create_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(
        required=True, default=lambda self: self.env._("New RCV Read"), copy=False
    )
    period_id = fields.Many2one(
        "l10n_cl.dte.rcv.period",
        required=True,
        index=True,
        ondelete="cascade",
        check_company=True,
    )
    company_id = fields.Many2one(
        "res.company", required=True, index=True, ondelete="cascade"
    )
    operation = fields.Selection(RCV_OPERATIONS, required=True, index=True)
    requested_by = fields.Many2one(
        "res.users", required=True, default=lambda self: self.env.user
    )
    scheduled_for = fields.Date(
        index=True,
        copy=False,
        readonly=True,
        help="Chilean local date for which the daily read was scheduled.",
    )
    source = fields.Selection(RCV_SOURCES, default="sii", required=True)
    state = fields.Selection(
        [
            ("queued", "Queued"),
            ("running", "Running"),
            ("pending", "Pending at SII"),
            ("partial", "Partial"),
            ("success", "Complete"),
            ("failed", "Failed"),
        ],
        default="queued",
        required=True,
        index=True,
    )
    started_at = fields.Datetime(readonly=True)
    finished_at = fields.Datetime(readonly=True)
    error_code = fields.Char(readonly=True)
    error_message = fields.Text(readonly=True)
    response_hash = fields.Char(
        readonly=True, help="SHA-256 of the SII response or of the imported file."
    )
    source_file_id = fields.Many2one(
        "ir.attachment", string="Source File", readonly=True, ondelete="set null"
    )
    detail_count = fields.Integer(string="Stored Documents", readonly=True)
    summary_count = fields.Integer(string="Summary Rows", readonly=True)
    period_year = fields.Integer(related="period_id.year", store=True)
    period_month = fields.Integer(related="period_id.month", store=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    summary_ids = fields.One2many("l10n_cl.dte.rcv.summary", "sync_id")
    section_ids = fields.One2many("l10n_cl.dte.rcv.section", "sync_id")
    document_ids = fields.One2many("l10n_cl.dte.rcv.document", "last_sync_id")
    observation_ids = fields.One2many("l10n_cl.dte.rcv.observation", "sync_id")
    boleta_daily_ids = fields.One2many("l10n_cl.dte.rcv.boleta.daily", "sync_id")

    _sql_constraints = [
        (
            "scheduled_sync_day_uniq",
            "unique(company_id, period_id, operation, scheduled_for)",
            "A daily read is already scheduled for this period and day.",
        ),
    ]

    def _on_rcv_documents_stored(self):
        """Run after the normalizer stored the documents of this read.

        Extension point for later integrations, such as reconciling the stored
        documents with journal entries. The base implementation does nothing.
        """
        self.ensure_one()
        return True

    # SII read

    def _run_sii_read(self):
        """Read this queued period from the SII and store the result.

        Every failure is recorded on the read; it is never raised, so one
        failed read does not stop the others processed by the cron.
        """
        self.ensure_one()
        self.write(
            {
                "state": "running",
                "started_at": fields.Datetime.now(),
                "error_code": False,
                "error_message": False,
            }
        )
        try:
            with self.env.cr.savepoint():
                sections = SiiRcvClient(self.env).fetch(self)
                state = RcvNormalizer(self.env).apply(self, sections)
        except UserError as error:
            code = getattr(error, "code", None) or type(error).__name__
            return self._finish_failed(code, str(error)[:MAX_ERROR_LENGTH])
        except Exception as error:  # pylint: disable=broad-except
            # Unexpected errors may carry request data: keep only their type.
            # Recording them keeps the read from being retried forever.
            _logger.warning(
                "RCV read %s failed unexpectedly (%s).", self.id, type(error).__name__
            )
            return self._finish_failed(
                type(error).__name__,
                self.env._(
                    "The SII read could not be completed. Check the certificate "
                    "and the access to the SII, or import the CSV."
                ),
            )
        now = fields.Datetime.now()
        self.write({"state": state, "finished_at": now})
        self.period_id.write(
            {"state": state, "last_sync_at": now, "current_sync_id": self.id}
        )
        return state

    def _finish_failed(self, code, message):
        """Record a failed read; a period keeps its last stored read, if any."""
        self.write(
            {
                "state": "failed",
                "finished_at": fields.Datetime.now(),
                "error_code": code,
                "error_message": message,
            }
        )
        if not self.period_id.current_sync_id:
            self.period_id.state = "failed"
        return "failed"

    @api.model
    def _cron_process_pending(self, batch_size=1):
        """Process the oldest queued SII reads; report progress to the cron."""
        domain = [("state", "=", "queued"), ("source", "=", "sii")]
        queued = self.search(domain, order="create_date, id", limit=batch_size)
        for sync in queued:
            sync.with_company(sync.company_id)._run_sii_read()
        self.env["ir.cron"]._notify_progress(
            done=len(queued), remaining=self.search_count(domain)
        )
        return True

    @api.model
    def _trigger_processing(self):
        self.env.ref("l10n_cl_dte_rcv.ir_cron_rcv_process_reads").sudo()._trigger()

    @api.model
    def _cron_schedule_daily_reads(self):
        """Queue today's read of the current and previous month.

        Only companies that enabled the daily read are read, on behalf of their
        read user. A period already scheduled today is not queued again.
        """
        today = fields.Date.context_today(self.with_context(tz=SII_TIMEZONE))
        previous = (
            (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        )
        companies = (
            self.env["res.company"].sudo().search([("dte_rcv_daily_read", "=", True)])
        )
        queued = 0
        for company in companies:
            if not company._dte_rcv_read_user_is_valid():
                _logger.warning(
                    "Daily RCV read skipped for company %s: its read user is "
                    "inactive, not a DTE manager or lacks access to it.",
                    company.id,
                )
                continue
            for year, month in ((today.year, today.month), previous):
                for operation in SII_OPERATIONS:
                    queued += self._schedule_daily_read(
                        company, year, month, operation, today
                    )
        if queued:
            self._trigger_processing()
        return queued

    @api.model
    def _schedule_daily_read(self, company, year, month, operation, today):
        """Queue one daily read; return 1 when a read was queued, else 0."""
        values = {
            "company_id": company.id,
            "year": year,
            "month": month,
            "operation": operation,
        }
        period_model = self.env["l10n_cl.dte.rcv.period"].sudo().with_company(company)
        period = period_model.search(
            [(key, "=", value) for key, value in values.items()], limit=1
        ) or period_model.create(values)
        scheduled = self.sudo().search_count(
            [("period_id", "=", period.id), ("scheduled_for", "=", today)]
        )
        if scheduled or period._active_sii_read():
            return 0
        try:
            with self.env.cr.savepoint():
                period._queue_sii_read(
                    company.dte_rcv_read_user_id, scheduled_for=today
                )
        except psycopg2.IntegrityError:
            _logger.info(
                "Daily RCV read already queued for company %s, %04d-%02d %s.",
                company.id,
                year,
                month,
                operation,
            )
            return 0
        return 1
