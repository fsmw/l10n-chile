# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import _, fields, models


class RcvSync(models.Model):
    _name = "l10n.cl.rcv.sync"
    _description = "SII RCV synchronization"
    _order = "create_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, default="Nueva sincronización", copy=False)
    period_id = fields.Many2one(
        "l10n.cl.rcv.period", required=True, index=True, ondelete="cascade"
    )
    company_id = fields.Many2one(
        "res.company", required=True, index=True, ondelete="cascade"
    )
    operation = fields.Selection(
        [("purchase", "Compras"), ("sale", "Ventas"), ("boleta", "Ventas Boletas")],
        required=True,
        index=True,
    )
    requested_by = fields.Many2one(
        "res.users", required=True, default=lambda self: self.env.user
    )
    scheduled_for = fields.Date(
        index=True,
        copy=False,
        help="Chilean local date for which this scheduled read was enqueued.",
    )
    source = fields.Selection(
        [("sii", "SII"), ("csv", "CSV")], default="sii", required=True
    )
    state = fields.Selection(
        [
            ("queued", "En cola"),
            ("running", "En ejecución"),
            ("pending", "Pendiente SII"),
            ("partial", "Parcial"),
            ("success", "Completada"),
            ("failed", "Fallida"),
        ],
        default="queued",
        required=True,
        index=True,
    )
    started_at = fields.Datetime(readonly=True)
    finished_at = fields.Datetime(readonly=True)
    error_code = fields.Char(readonly=True)
    error_message = fields.Text(readonly=True)
    response_hash = fields.Char(readonly=True)
    source_file_id = fields.Many2one(
        "ir.attachment", readonly=True, ondelete="set null"
    )
    detail_count = fields.Integer(readonly=True)
    summary_count = fields.Integer(readonly=True)
    period_year = fields.Integer(related="period_id.year", store=True)
    period_month = fields.Integer(related="period_id.month", store=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    summary_ids = fields.One2many("l10n.cl.rcv.summary", "sync_id")
    section_ids = fields.One2many("l10n.cl.rcv.section", "sync_id")
    document_ids = fields.One2many("l10n.cl.rcv.document", "last_sync_id")

    _sql_constraints = [
        (
            "scheduled_sync_day_uniq",
            "unique(company_id, period_id, operation, scheduled_for)",
            "Ya existe una sincronización programada para este período y día.",
        ),
    ]

    def _process(self):
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
            data = self.env["l10n.cl.rcv.sii.client"]._fetch(self)
            result_state = self.env["l10n.cl.rcv.normalizer"]._apply(self, data)
            self.write({"state": result_state, "finished_at": fields.Datetime.now()})
            self.period_id.write(
                {"state": result_state, "last_sync_at": fields.Datetime.now()}
            )
            self.env["l10n.cl.rcv.auto.draft"].process_sync(self)
        except Exception as exc:
            # The fetch is outside the per-section savepoints. Persist only safe,
            # controlled messages; unexpected exceptions may contain request data.
            message = (
                str(exc)[:500]
                if exc.__class__.__name__ == "UserError"
                else _(
                    "No se pudo completar la consulta RCV. "
                    "Verifique el certificado y el acceso al SII, o importe el CSV."
                )
            )
            result_state = "partial" if self.section_ids else "failed"
            self.write(
                {
                    "state": result_state,
                    "finished_at": fields.Datetime.now(),
                    "error_code": type(exc).__name__,
                    "error_message": message,
                }
            )
            self.period_id.write({"state": result_state})
        return True
