# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import TaxReportsTestCase


@tagged("post_install", "-at_install")
class TestTaxReportsAccess(TaxReportsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.reader = cls._user("tax-reports-reader", "account.group_account_readonly")
        cls.manager = cls._user("tax-reports-manager", "account.group_account_manager")
        cls.employee = cls._user("tax-reports-employee", "base.group_user")
        cls._document("out_invoice", "33", 1000, cls.sale_vat)

    def _f29(self, user, company=None):
        return (
            self.env["l10n_cl.f29.report"]
            .with_user(user)
            .create(
                {
                    "company_id": (company or self.company).id,
                    "year": 2026,
                    "month": "8",
                }
            )
        )

    def test_reader_runs_reports(self):
        report = self._f29(self.reader)
        report.action_compute()
        self.assertEqual(report.total_debits, 190)
        balance = (
            self.env["l10n_cl.eight.column.balance"]
            .with_user(self.reader)
            .create({"date_from": "2026-01-01", "date_to": "2026-12-31"})
        )
        balance.action_compute()
        self.assertEqual(balance.total_debit, balance.total_credit)

    def test_reader_cannot_configure_boxes(self):
        box = self.env.ref("l10n_cl_dte_tax_reports.f29_box_sale_invoice")
        self.assertTrue(box.with_user(self.reader).name)
        with self.assertRaises(AccessError):
            box.with_user(self.reader).write({"amount_code": "999"})
        box.with_user(self.manager).write({"amount_code": "502"})

    def test_employee_has_no_access(self):
        with self.assertRaises(AccessError):
            self._f29(self.employee)
        with self.assertRaises(AccessError):
            self.env["l10n_cl.eight.column.balance"].with_user(self.employee).create(
                {"date_from": "2026-01-01", "date_to": "2026-12-31"}
            )

    def test_other_company_is_forbidden(self):
        other_company = self.setup_other_company(name="Forbidden Company")["company"]
        with self.assertRaises(AccessError):
            self._f29(self.reader, company=other_company)
