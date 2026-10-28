# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import base64
import csv
import io
import logging

from psycopg2 import IntegrityError

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class RcvPeriod(models.Model):
    _name = "l10n.cl.rcv.period"
    _description = "SII RCV period"
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
    operation = fields.Selection(
        [("purchase", "Compras"), ("sale", "Ventas"), ("boleta", "Ventas Boletas")],
        required=True,
        index=True,
    )
    state = fields.Selection(
        [
            ("not_synced", "Sin sincronizar"),
            ("pending", "Pendiente"),
            ("partial", "Parcial"),
            ("success", "Completado"),
            ("failed", "Fallido"),
        ],
        default="not_synced",
        required=True,
        index=True,
    )
    last_sync_at = fields.Datetime(readonly=True)
    current_sync_id = fields.Many2one(
        "l10n.cl.rcv.sync", readonly=True, ondelete="set null"
    )
    document_count = fields.Integer(compute="_compute_counts")
    summary_count = fields.Integer(compute="_compute_counts")
    currency_id = fields.Many2one(related="company_id.currency_id", store=True)

    _sql_constraints = [
        (
            "period_company_month_operation_uniq",
            "unique(company_id, year, month, operation)",
            "Ya existe este período RCV para la compañía.",
        ),
        (
            "period_month_valid",
            "check(month >= 1 and month <= 12)",
            "El mes debe estar entre 1 y 12.",
        ),
    ]

    @api.depends("year", "month", "operation")
    def _compute_name(self):
        labels = dict(self._fields["operation"].selection)
        for record in self:
            record.name = "%04d-%02d · %s" % (
                record.year,
                record.month,
                labels.get(record.operation, ""),
            )

    def _compute_counts(self):
        for record in self:
            record.document_count = self.env["l10n.cl.rcv.document"].search_count(
                [
                    ("company_id", "=", record.company_id.id),
                    ("operation", "=", record.operation),
                    ("tax_period", "=", "%04d%02d" % (record.year, record.month)),
                ]
            )
            record.summary_count = (
                self.env["l10n.cl.rcv.summary"].search_count(
                    [
                        ("sync_id", "=", record.current_sync_id.id),
                    ]
                )
                if record.current_sync_id
                else 0
            )

    def action_sync(self):
        for period in self:
            if period.operation == "boleta":
                sync = self.env["l10n.cl.rcv.sync"].create(
                    {
                        "period_id": period.id,
                        "company_id": period.company_id.id,
                        "operation": period.operation,
                        "requested_by": self.env.user.id,
                        "state": "pending",
                        "error_message": _(
                            "La consulta directa de boletas está pendiente de validar "
                            "con el SII."
                        ),
                    }
                )
                period.write(
                    {
                        "state": "pending",
                        "current_sync_id": sync.id,
                        "last_sync_at": fields.Datetime.now(),
                    }
                )
                continue
            sync = self.env["l10n.cl.rcv.sync"].create(
                {
                    "period_id": period.id,
                    "company_id": period.company_id.id,
                    "operation": period.operation,
                    "requested_by": self.env.user.id,
                }
            )
            period.write({"state": "pending", "current_sync_id": sync.id})
        return True

    @api.model
    def action_open_year(self, year=None):
        year = int(year or fields.Date.today().year)
        company = self.env.company
        periods = self.sudo()
        for month in range(1, 13):
            for operation in ("purchase", "sale", "boleta"):
                period = periods.search(
                    [
                        ("company_id", "=", company.id),
                        ("year", "=", year),
                        ("month", "=", month),
                        ("operation", "=", operation),
                    ],
                    limit=1,
                )
                if not period:
                    periods.create(
                        {
                            "company_id": company.id,
                            "year": year,
                            "month": month,
                            "operation": operation,
                        }
                    )
        action = self.env.ref("l10n_cl_rcv.action_rcv_period").read()[0]
        action["domain"] = [("company_id", "=", company.id), ("year", "=", year)]
        action["context"] = {"search_default_group_month": 1}
        return action

    def action_export_csv(self):
        self.ensure_one()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "tipo_fila",
                "periodo",
                "operacion",
                "estado_sii",
                "tipo_documento",
                "rut_contraparte",
                "razon_social",
                "folio",
                "fecha",
                "conteo",
                "neto",
                "exento",
                "iva",
                "total",
                "conciliacion",
                "fuente",
            ]
        )
        docs = self.env["l10n.cl.rcv.document"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("operation", "=", self.operation),
                ("tax_period", "=", "%04d%02d" % (self.year, self.month)),
            ],
            order="document_date, folio",
        )
        for doc in docs:
            writer.writerow(
                [
                    "DETALLE",
                    doc.tax_period,
                    doc.operation,
                    doc.sii_state,
                    doc.document_type_code,
                    doc.counterpart_vat,
                    doc.issuer_name,
                    doc.folio,
                    doc.document_date or "",
                    1,
                    doc.net_amount,
                    doc.exempt_amount,
                    doc.vat_amount,
                    doc.total_amount,
                    doc.reconciliation_state,
                    doc.source,
                ]
            )
        for line in self.current_sync_id.summary_ids:
            writer.writerow(
                [
                    "RESUMEN",
                    "%04d%02d" % (self.year, self.month),
                    self.operation,
                    line.sii_state,
                    line.document_type_code,
                    "",
                    "",
                    "",
                    "",
                    line.document_count,
                    line.net_amount,
                    line.exempt_amount,
                    line.vat_amount,
                    line.total_amount,
                    self.state,
                    self.current_sync_id.source,
                ]
            )
        attachment = self.env["ir.attachment"].create(
            {
                "name": "RCV-%04d-%02d-%s.csv"
                % (self.year, self.month, self.operation),
                "type": "binary",
                "datas": base64.b64encode(output.getvalue().encode("utf-8-sig")),
                "res_model": self._name,
                "res_id": self.id,
                "company_id": self.company_id.id,
            }
        )
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "self",
        }

    def action_print_report(self):
        self.ensure_one()
        return self.env.ref("l10n_cl_rcv.action_report_rcv_period").report_action(self)

    def action_open_documents(self):
        self.ensure_one()
        action = self.env.ref("l10n_cl_rcv.action_rcv_document").read()[0]
        action["domain"] = [
            ("company_id", "=", self.company_id.id),
            ("operation", "=", self.operation),
            ("tax_period", "=", "%04d%02d" % (self.year, self.month)),
        ]
        return action

    @api.model
    def _cron_process_pending(self, limit=5):
        runs = self.env["l10n.cl.rcv.sync"].search(
            [("state", "=", "queued")], limit=limit, order="create_date, id"
        )
        for run in runs:
            run.with_company(run.company_id)._process()

    @api.model
    def _cron_schedule_daily_purchases(self):
        today = fields.Date.context_today(self.with_context(tz="America/Santiago"))
        if today.month == 1:
            periods = [(today.year, 1), (today.year - 1, 12)]
        else:
            periods = [(today.year, today.month), (today.year, today.month - 1)]

        company_model = self.env["res.company"].sudo()
        period_model = self.env["l10n.cl.rcv.period"].sudo()
        sync_model = self.env["l10n.cl.rcv.sync"].sudo()
        companies = company_model.search(
            [("l10n_cl_rcv_automation_enabled", "=", True)]
        )
        for company in companies:
            user = company.l10n_cl_rcv_service_user_id
            if not user or not user.active or company not in user.company_ids:
                _logger.warning(
                    "Skipping daily RCV for company %s: automation user is inactive "
                    "or lacks company access.",
                    company.id,
                )
                continue
            self.env["l10n.cl.rcv.auto.draft"].process_existing_purchases(
                company, periods
            )
            for year, month in periods:
                domain = [
                    ("company_id", "=", company.id),
                    ("year", "=", year),
                    ("month", "=", month),
                    ("operation", "=", "purchase"),
                ]
                try:
                    with self.env.cr.savepoint():
                        period = period_model.search(domain, limit=1)
                        if not period:
                            period = period_model.create(
                                {
                                    "company_id": company.id,
                                    "year": year,
                                    "month": month,
                                    "operation": "purchase",
                                }
                            )
                        existing = sync_model.search(
                            [
                                ("period_id", "=", period.id),
                                ("company_id", "=", company.id),
                                ("operation", "=", "purchase"),
                                ("scheduled_for", "=", today),
                            ],
                            limit=1,
                        )
                        if existing:
                            continue
                        sync = sync_model.create(
                            {
                                "period_id": period.id,
                                "company_id": company.id,
                                "operation": "purchase",
                                "requested_by": user.id,
                                "source": "sii",
                                "scheduled_for": today,
                            }
                        )
                        period.write({"state": "pending", "current_sync_id": sync.id})
                except IntegrityError:
                    _logger.info(
                        "Daily RCV read already queued for company %s, %04d-%02d.",
                        company.id,
                        year,
                        month,
                    )
        return True
