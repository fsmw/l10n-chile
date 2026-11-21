# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import calendar
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .f29_box import SALE_MOVE_TYPES

MONTHS = [(str(month), calendar.month_name[month]) for month in range(1, 13)]


class L10nClF29Report(models.TransientModel):
    """Monthly F29 worksheet: VAT debits and credits, withholdings and PPM."""

    _name = "l10n_cl.f29.report"
    _description = "F29 Monthly VAT Worksheet"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        domain=lambda self: [("id", "in", self.env.companies.ids)],
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    year = fields.Integer(required=True, default=lambda self: date.today().year)
    month = fields.Selection(
        MONTHS, required=True, default=lambda self: str(date.today().month)
    )
    date_from = fields.Date(compute="_compute_dates")
    date_to = fields.Date(compute="_compute_dates")
    previous_credit_remainder = fields.Monetary(
        string="Credit Remainder from Previous Period",
        help="Remaining VAT credit carried over from the previous F29 (box 504).",
    )
    ppm_rate = fields.Float(
        string="PPM Rate (%)",
        digits=(16, 3),
        compute="_compute_rates",
        store=True,
        readonly=False,
    )
    proportional_factor = fields.Float(
        string="Proportional Factor (%)",
        digits=(16, 2),
        compute="_compute_rates",
        store=True,
        readonly=False,
    )
    line_ids = fields.One2many("l10n_cl.f29.report.line", "report_id")
    total_debits = fields.Monetary(readonly=True)
    total_credits = fields.Monetary(readonly=True)
    vat_payable = fields.Monetary(string="VAT Payable", readonly=True)
    credit_remainder = fields.Monetary(
        string="Remaining Credit for Next Period", readonly=True
    )
    total_withholdings = fields.Monetary(readonly=True)
    ppm_base = fields.Monetary(string="PPM Base", readonly=True)
    ppm_amount = fields.Monetary(string="PPM", readonly=True)
    total_payable = fields.Monetary(readonly=True)

    @api.depends("year", "month")
    def _compute_dates(self):
        for report in self:
            if not report.year or not report.month:
                report.date_from = report.date_to = False
                continue
            month = int(report.month)
            last_day = calendar.monthrange(report.year, month)[1]
            report.date_from = date(report.year, month, 1)
            report.date_to = date(report.year, month, last_day)

    @api.depends("company_id")
    def _compute_rates(self):
        for report in self:
            report.ppm_rate = report.company_id.l10n_cl_f29_ppm_rate
            report.proportional_factor = (
                report.company_id.l10n_cl_f29_proportional_factor
            )

    def action_compute(self):
        self.ensure_one()
        if not 1900 < self.year < 3000:
            raise UserError(_("Please enter a valid year."))
        company = self.company_id
        boxes = self.env["l10n_cl.f29.box"]._get_active_boxes(company)
        line_vals = []
        effects = dict.fromkeys(
            ("debit", "debit_reduce", "credit", "credit_reduce", "withholding"), 0.0
        )
        for box in boxes:
            count, amount = box._compute_values(
                company, self.date_from, self.date_to, self.proportional_factor
            )
            if box.total_effect in effects:
                effects[box.total_effect] += amount
            line_vals.append(self._prepare_box_line(box, count, amount))
        totals = self._compute_totals(effects)
        line_vals += self._prepare_total_lines(totals)
        self.line_ids.unlink()
        self.write(dict(totals, line_ids=[fields.Command.create(v) for v in line_vals]))
        return self._reopen_action()

    def _prepare_box_line(self, box, count, amount):
        return {
            "sequence": box.sequence,
            "section": box.section,
            "box_id": box.id,
            "name": box.name,
            "count_code": box.count_code,
            "count": count if box.count_code else 0,
            "amount_code": box.amount_code,
            "amount": amount if box.amount_source != "none" else 0.0,
        }

    def _compute_totals(self, effects):
        currency = self.company_id.currency_id
        debits = effects["debit"] - effects["debit_reduce"]
        total_credits = (
            self.previous_credit_remainder
            + effects["credit"]
            - effects["credit_reduce"]
        )
        balance = currency.round(debits - total_credits)
        ppm_base = self._compute_ppm_base()
        ppm_amount = currency.round(ppm_base * self.ppm_rate / 100.0)
        vat_payable = max(balance, 0.0)
        withholdings = effects["withholding"]
        return {
            "total_debits": debits,
            "total_credits": total_credits,
            "vat_payable": vat_payable,
            "credit_remainder": max(-balance, 0.0),
            "total_withholdings": withholdings,
            "ppm_base": ppm_base,
            "ppm_amount": ppm_amount,
            "total_payable": vat_payable + withholdings + ppm_amount,
        }

    def _compute_ppm_base(self):
        """Net sales of the period: untaxed amount of customer documents."""
        moves = self.env["account.move"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("state", "=", "posted"),
                ("date", ">=", self.date_from),
                ("date", "<=", self.date_to),
                ("move_type", "in", SALE_MOVE_TYPES),
            ]
        )
        return self.company_id.currency_id.round(
            sum(moves.mapped("amount_untaxed_signed"))
        )

    def _prepare_total_lines(self, totals):
        rows = [
            (
                "504",
                _("Credit remainder from previous period"),
                self.previous_credit_remainder,
            ),
            ("538", _("Total debits"), totals["total_debits"]),
            ("537", _("Total credits"), totals["total_credits"]),
            ("89", _("VAT payable"), totals["vat_payable"]),
            ("77", _("Remaining credit for next period"), totals["credit_remainder"]),
            ("563", _("PPM base (net sales)"), totals["ppm_base"]),
            ("62", _("PPM (rate %s%%)", self.ppm_rate), totals["ppm_amount"]),
            ("", _("Total withholdings"), totals["total_withholdings"]),
            ("91", _("Total to pay"), totals["total_payable"]),
        ]
        return [
            {
                "sequence": 1000 + index,
                "section": "total",
                "line_type": "total",
                "name": name,
                "amount_code": code,
                "amount": amount,
            }
            for index, (code, name, amount) in enumerate(rows)
        ]

    def _reopen_action(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_print_pdf(self):
        self.ensure_one()
        if not self.line_ids:
            self.action_compute()
        return self.env.ref("l10n_cl_dte_tax_reports.action_report_f29").report_action(
            self
        )
