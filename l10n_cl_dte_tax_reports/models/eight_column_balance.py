# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError

BALANCE_GROUPS = ("asset", "liability", "equity")
RESULT_GROUPS = ("income", "expense")
AMOUNT_FIELDS = (
    "debit",
    "credit",
    "debit_balance",
    "credit_balance",
    "assets",
    "liabilities",
    "losses",
    "gains",
)


class L10nClEightColumnBalance(models.TransientModel):
    """Chilean eight-column balance (balance tributario de 8 columnas).

    Balance sheet accounts accumulate every posted entry up to the end date;
    income and expense accounts only the entries of the date range. Older
    results and current-year earnings accounts are grouped in one line so the
    report always balances.
    """

    _name = "l10n_cl.eight.column.balance"
    _description = "Eight-Column Balance"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        domain=lambda self: [("id", "in", self.env.companies.ids)],
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date(
        required=True, default=lambda self: date(date.today().year, 1, 1)
    )
    date_to = fields.Date(required=True, default=fields.Date.context_today)
    line_ids = fields.One2many("l10n_cl.eight.column.balance.line", "report_id")
    total_debit = fields.Monetary(compute="_compute_totals")
    total_credit = fields.Monetary(compute="_compute_totals")
    total_debit_balance = fields.Monetary(compute="_compute_totals")
    total_credit_balance = fields.Monetary(compute="_compute_totals")
    total_assets = fields.Monetary(compute="_compute_totals")
    total_liabilities = fields.Monetary(compute="_compute_totals")
    total_losses = fields.Monetary(compute="_compute_totals")
    total_gains = fields.Monetary(compute="_compute_totals")

    @api.depends(*(f"line_ids.{name}" for name in AMOUNT_FIELDS))
    def _compute_totals(self):
        for report in self:
            for name in AMOUNT_FIELDS:
                report[f"total_{name}"] = sum(report.line_ids.mapped(name))

    def _get_subtotal_lines(self):
        return self.line_ids.filtered(lambda line: line.line_type != "result")

    def _get_result_line(self):
        return self.line_ids.filtered(lambda line: line.line_type == "result")

    def _line_domain(self):
        return [
            ("company_id", "=", self.company_id.id),
            ("parent_state", "=", "posted"),
            ("date", "<=", self.date_to),
        ]

    def _read_accounts(self, extra_domain):
        line_model = self.env["account.move.line"].with_company(self.company_id)
        groups = line_model._read_group(
            self._line_domain() + extra_domain,
            groupby=["account_id"],
            aggregates=["debit:sum", "credit:sum"],
        )
        return [(account, debit, credit) for account, debit, credit in groups]

    def _collect_account_rows(self):
        balance_rows = self._read_accounts(
            [
                ("account_id.internal_group", "in", BALANCE_GROUPS),
                ("account_id.account_type", "!=", "equity_unaffected"),
            ]
        )
        result_rows = self._read_accounts(
            [
                ("account_id.internal_group", "in", RESULT_GROUPS),
                ("date", ">=", self.date_from),
            ]
        )
        return sorted(balance_rows + result_rows, key=lambda row: row[0].code or "")

    def _collect_unallocated(self):
        """Debits and credits of results not belonging to the date range."""
        domain = self._line_domain() + [
            "|",
            ("account_id.account_type", "=", "equity_unaffected"),
            "&",
            ("account_id.internal_group", "in", RESULT_GROUPS),
            ("date", "<", self.date_from),
        ]
        [(debit, credit)] = self.env["account.move.line"]._read_group(
            domain, aggregates=["debit:sum", "credit:sum"]
        )
        return debit or 0.0, credit or 0.0

    def _prepare_line(self, debit, credit, is_balance_sheet, **values):
        currency = self.company_id.currency_id
        balance = currency.round(debit - credit)
        debit_balance = max(balance, 0.0)
        credit_balance = max(-balance, 0.0)
        values.update(
            debit=debit,
            credit=credit,
            debit_balance=debit_balance,
            credit_balance=credit_balance,
            assets=debit_balance if is_balance_sheet else 0.0,
            liabilities=credit_balance if is_balance_sheet else 0.0,
            losses=0.0 if is_balance_sheet else debit_balance,
            gains=0.0 if is_balance_sheet else credit_balance,
        )
        return values

    def _prepare_result_line(self, line_vals):
        losses = sum(vals["losses"] for vals in line_vals)
        gains = sum(vals["gains"] for vals in line_vals)
        result = self.company_id.currency_id.round(gains - losses)
        values = dict.fromkeys(AMOUNT_FIELDS, 0.0)
        if result >= 0:
            values.update(losses=result, liabilities=result)
            name = _("Profit for the period")
        else:
            values.update(gains=-result, assets=-result)
            name = _("Loss for the period")
        return dict(values, line_type="result", name=name, sequence=100000)

    def action_compute(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_("The start date must be before the end date."))
        line_vals = []
        for sequence, (account, debit, credit) in enumerate(
            self._collect_account_rows(), start=1
        ):
            line_vals.append(
                self._prepare_line(
                    debit,
                    credit,
                    account.internal_group in BALANCE_GROUPS,
                    sequence=sequence,
                    line_type="account",
                    account_id=account.id,
                    name=account.display_name,
                )
            )
        debit, credit = self._collect_unallocated()
        if debit or credit:
            line_vals.append(
                self._prepare_line(
                    debit,
                    credit,
                    True,
                    sequence=99999,
                    line_type="unallocated",
                    name=_("Unallocated earnings (previous periods)"),
                )
            )
        line_vals.append(self._prepare_result_line(line_vals))
        self.line_ids.unlink()
        self.line_ids = [fields.Command.create(vals) for vals in line_vals]
        return True

    def action_view_lines(self):
        self.ensure_one()
        self.action_compute()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "l10n_cl_dte_tax_reports.action_eight_column_balance_line"
        )
        action["domain"] = [("report_id", "=", self.id)]
        action["display_name"] = _(
            "Eight-Column Balance %(date_from)s - %(date_to)s",
            date_from=self.date_from,
            date_to=self.date_to,
        )
        return action

    def action_print_pdf(self):
        self.ensure_one()
        self.action_compute()
        return self.env.ref(
            "l10n_cl_dte_tax_reports.action_report_eight_column_balance"
        ).report_action(self)
