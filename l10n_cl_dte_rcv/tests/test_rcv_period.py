# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import RcvTestCase


@tagged("post_install", "-at_install")
class TestRcvPeriod(RcvTestCase):
    def test_name_and_tax_period(self):
        period = self._period(year=2026, month=3)
        self.assertEqual(period.tax_period, "202603")
        self.assertTrue(period.name.startswith("2026-03 · "))
        self.assertEqual(period.state, "not_synced")
        self.assertEqual(period.currency_id, self.company.currency_id)

    def test_month_and_year_are_validated(self):
        with self.assertRaises(ValidationError):
            self._period(year=26)
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
            self._period(month=13)

    def test_period_is_unique_per_company_month_and_operation(self):
        self._period()
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
            self._period()

    def test_open_year_creates_missing_periods_once(self):
        self._period(year=2025, month=1)
        action = self.env(user=self.reader)["l10n_cl.dte.rcv.period"].action_open_year(
            2025
        )
        periods = self.env["l10n_cl.dte.rcv.period"].search(action["domain"])
        self.assertEqual(len(periods), 36)
        self.assertEqual(
            set(periods.mapped("operation")), {"purchase", "sale", "boleta"}
        )
        self.env["l10n_cl.dte.rcv.year.wizard"].with_user(self.reader).create(
            {"year": 2025}
        ).action_open_year()
        self.assertEqual(
            self.env["l10n_cl.dte.rcv.period"].search_count(action["domain"]), 36
        )

    def test_open_documents_filters_the_period(self):
        period = self._period()
        action = period.action_open_documents()
        self.assertIn(("tax_period", "=", "202608"), action["domain"])
        self.assertIn(("operation", "=", "purchase"), action["domain"])
        self.assertEqual(period.document_count, 0)
        self.assertEqual(period.summary_count, 0)
