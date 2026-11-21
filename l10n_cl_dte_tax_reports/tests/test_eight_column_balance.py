# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import TaxReportsTestCase

COLUMNS = (
    "debit",
    "credit",
    "debit_balance",
    "credit_balance",
    "assets",
    "liabilities",
    "losses",
    "gains",
)


@tagged("post_install", "-at_install")
class TestEightColumnBalance(TaxReportsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        data = cls.company_data
        cls.receivable = data["default_account_receivable"]
        cls.payable = data["default_account_payable"]
        cls.revenue = data["default_account_revenue"]
        cls.expense = data["default_account_expense"]
        cls._entry("2025-12-10", [(cls.receivable, 500, 0), (cls.revenue, 0, 500)])
        cls._entry("2026-03-01", [(cls.receivable, 1000, 0), (cls.revenue, 0, 1000)])
        cls._entry("2026-03-05", [(cls.expense, 300, 0), (cls.payable, 0, 300)])
        cls._entry("2026-05-01", [(cls.receivable, 50, 0), (cls.revenue, 0, 50)])

    def _report(self, date_from="2026-01-01", date_to="2026-03-31", company=None):
        report = self.env["l10n_cl.eight.column.balance"].create(
            {
                "company_id": (company or self.company).id,
                "date_from": date_from,
                "date_to": date_to,
            }
        )
        report.action_compute()
        return report

    def _values(self, line):
        return {column: line[column] for column in COLUMNS}

    def _line(self, report, account):
        return report.line_ids.filtered(lambda line: line.account_id == account)

    def test_account_columns(self):
        report = self._report()
        self.assertEqual(
            self._values(self._line(report, self.receivable)),
            dict.fromkeys(COLUMNS, 0.0)
            | {"debit": 1500, "debit_balance": 1500, "assets": 1500},
        )
        self.assertEqual(
            self._values(self._line(report, self.payable)),
            dict.fromkeys(COLUMNS, 0.0)
            | {"credit": 300, "credit_balance": 300, "liabilities": 300},
        )
        self.assertEqual(
            self._values(self._line(report, self.revenue)),
            dict.fromkeys(COLUMNS, 0.0)
            | {"credit": 1000, "credit_balance": 1000, "gains": 1000},
        )
        self.assertEqual(
            self._values(self._line(report, self.expense)),
            dict.fromkeys(COLUMNS, 0.0)
            | {"debit": 300, "debit_balance": 300, "losses": 300},
        )
        unallocated = report.line_ids.filtered(
            lambda line: line.line_type == "unallocated"
        )
        self.assertEqual(unallocated.credit, 500)
        self.assertEqual(unallocated.liabilities, 500)

    def test_profit_line_balances_report(self):
        report = self._report()
        result = report.line_ids.filtered(lambda line: line.line_type == "result")
        self.assertEqual(result.name, "Profit for the period")
        self.assertEqual((result.losses, result.liabilities), (700, 700))
        self.assertEqual(report.total_debit, report.total_credit)
        self.assertEqual(report.total_debit_balance, report.total_credit_balance)
        self.assertEqual((report.total_assets, report.total_liabilities), (1500, 1500))
        self.assertEqual((report.total_losses, report.total_gains), (1000, 1000))

    def test_loss_line(self):
        self._entry("2026-03-20", [(self.expense, 2000, 0), (self.payable, 0, 2000)])
        report = self._report()
        result = report.line_ids.filtered(lambda line: line.line_type == "result")
        self.assertEqual(result.name, "Loss for the period")
        self.assertEqual((result.gains, result.assets), (1300, 1300))
        self.assertEqual(report.total_assets, report.total_liabilities)
        self.assertEqual(report.total_losses, report.total_gains)

    def test_date_range(self):
        report = self._report(date_from="2026-04-01", date_to="2026-05-31")
        self.assertEqual(self._line(report, self.revenue).credit, 50)
        self.assertEqual(self._line(report, self.receivable).debit, 1550)
        self.assertFalse(self._line(report, self.expense))
        unallocated = report.line_ids.filtered(
            lambda line: line.line_type == "unallocated"
        )
        self.assertEqual(unallocated.credit - unallocated.debit, 1500 - 300)
        self.assertEqual(report.total_assets, report.total_liabilities)
        with self.assertRaises(UserError):
            self._report(date_from="2026-06-01", date_to="2026-05-31")

    def test_draft_entries_ignored(self):
        entry = self._entry(
            "2026-03-10", [(self.receivable, 77, 0), (self.revenue, 0, 77)]
        )
        entry.button_draft()
        report = self._report()
        self.assertEqual(self._line(report, self.receivable).debit, 1500)

    def test_company_isolation(self):
        other = self.setup_other_company(name="Other Chilean Company")
        other_company = other["company"]
        self.env.user.company_ids |= other_company
        self._entry(
            "2026-02-01",
            [
                (other["default_account_receivable"], 9000, 0),
                (other["default_account_revenue"], 0, 9000),
            ],
            company=other_company,
        )
        report = self._report()
        self.assertEqual(report.total_debit, 1800)
        other_report = self._report(company=other_company)
        self.assertEqual(other_report.total_gains, 9000)
        self.assertFalse(
            other_report.line_ids.account_id.filtered(
                lambda account: self.company in account.company_ids
            )
        )

    def test_view_and_print_actions(self):
        report = self._report()
        action = report.action_view_lines()
        self.assertEqual(action["res_model"], "l10n_cl.eight.column.balance.line")
        lines = self.env[action["res_model"]].search(action["domain"])
        self.assertEqual(lines, report.line_ids)
        html = self.env["ir.actions.report"]._render_qweb_html(
            "l10n_cl_dte_tax_reports.report_eight_column_balance", report.ids
        )[0]
        self.assertIn(b"Profit for the period", html)
