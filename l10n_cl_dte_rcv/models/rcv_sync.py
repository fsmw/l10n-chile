# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models

from .rcv_period import RCV_OPERATIONS, RCV_SOURCES


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

    def _on_rcv_documents_stored(self):
        """Run after the normalizer stored the documents of this read.

        Extension point for later integrations, such as reconciling the stored
        documents with journal entries. The base implementation does nothing.
        """
        self.ensure_one()
        return True
