# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from datetime import date, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "l10n_cl_currency_rate_bcentral")
class TestBcentralRateRangeWizard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.country_id = cls.env.ref("base.cl")
        cls.config = cls.company._get_l10n_cl_bcentral_config()
        cls.currency_usd = cls.env.ref("base.USD")
        cls.currency_utm = cls.env["res.currency"].search(
            [("name", "=", "UTM")], limit=1
        )
        if not cls.currency_utm:
            cls.currency_utm = cls.env["res.currency"].create(
                {
                    "name": "UTM",
                    "symbol": "UTM",
                    "rounding": 0.01,
                    "decimal_places": 2,
                }
            )

    def _rate_domain(self, currency, rate_date, company=None):
        return [
            ("currency_id", "=", currency.id),
            ("name", "=", rate_date),
            ("company_id", "=", (company or self.company).root_id.id),
        ]

    def test_currency_action_and_wizard_defaults(self):
        action = self.env.ref(
            "l10n_cl_currency_rate_bcentral.action_bcentral_rate_range_wizard"
        )
        view = self.env.ref(
            "l10n_cl_currency_rate_bcentral.view_currency_form_bcentral_rate_range"
        )
        self.assertEqual(action.res_model, "l10n.cl.bcentral.rate.range.wizard")
        self.assertEqual(action.target, "new")
        self.assertIn("Obtener Tasas", view.arch_db)
        wizard = self.env["l10n.cl.bcentral.rate.range.wizard"].with_context(
            active_model="res.currency", active_id=self.currency_usd.id
        )
        defaults = wizard.default_get(["currency_id", "date_from", "date_to"])
        self.assertEqual(defaults["currency_id"], self.currency_usd.id)
        self.assertEqual(defaults["date_from"], fields.Date.today())
        self.assertEqual(defaults["date_to"], fields.Date.today())

    def test_server_operation_denies_non_accounting_manager(self):
        user = self.env["res.users"].create(
            {
                "name": "Bcentral range user",
                "login": "bcentral-range-user",
                "company_id": self.company.id,
                "company_ids": [(6, 0, self.company.ids)],
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        config = self.config.with_user(user)
        with self.assertRaises(AccessError):
            config.action_fetch_currency_range(
                self.currency_usd, date(2026, 1, 1), date(2026, 1, 2)
            )

    def test_invalid_and_future_ranges_are_rejected(self):
        with self.assertRaises(ValidationError):
            self.config.action_fetch_currency_range(
                self.currency_usd, date(2026, 2, 2), date(2026, 2, 1)
            )
        with self.assertRaises(ValidationError):
            self.config.action_fetch_currency_range(
                self.currency_usd,
                fields.Date.today(),
                fields.Date.today() + timedelta(days=1),
            )

    def test_unsupported_currency_is_rejected(self):
        unsupported = self.env["res.currency"].search(
            [("name", "not in", ("UF", "USD", "EUR", "UTM"))], limit=1
        )
        with self.assertRaises(ValidationError):
            self.config.action_fetch_currency_range(
                unsupported, date(2026, 1, 1), date(2026, 1, 2)
            )

    def test_inclusive_range_creates_only_selected_currency_and_missing_rates(self):
        start, end = date(2000, 5, 4), date(2000, 5, 5)
        records = [
            {"date": date(2000, 5, 3), "value": 900, "status": "ok"},
            {"date": start, "value": 901, "status": "ok"},
            {"date": end, "value": 902, "status": "ok"},
            {"date": date(2000, 5, 6), "value": 903, "status": "ok"},
        ]
        with patch.object(
            type(self.config), "_fetch_source_records", return_value=records
        ) as fetch:
            result = self.config.action_fetch_currency_range(
                self.currency_usd, start, end
            )
        fetch.assert_called_once_with("USD", start, end)
        self.assertEqual((result["created"], result["failed"]), (2, 0))
        Rate = self.env["res.currency.rate"].sudo()
        self.assertTrue(Rate.search(self._rate_domain(self.currency_usd, start)))
        self.assertTrue(Rate.search(self._rate_domain(self.currency_usd, end)))
        self.assertFalse(
            Rate.search(self._rate_domain(self.currency_usd, date(2000, 5, 3)))
        )

    def test_retry_skips_and_preserves_existing_rate_and_sync_cursor(self):
        rate_date = date(2000, 5, 7)
        Rate = self.env["res.currency.rate"].sudo()
        existing = Rate.search(self._rate_domain(self.currency_usd, rate_date), limit=1)
        if not existing:
            existing = Rate.create(
                {
                    "name": rate_date,
                    "currency_id": self.currency_usd.id,
                    "company_id": self.company.root_id.id,
                    "rate": 1 / 777,
                }
            )
        else:
            existing.rate = 1 / 777
        cursor = date(2000, 4, 30)
        self.config.write(
            {
                "last_sync_date": cursor,
                "last_sync_status": "partial",
            }
        )
        with patch.object(
            type(self.config),
            "_fetch_source_records",
            return_value=[{"date": rate_date, "value": 999, "status": "ok"}],
        ):
            result = self.config.action_fetch_currency_range(
                self.currency_usd, rate_date, rate_date
            )
        existing.invalidate_recordset()
        self.assertEqual(result["skipped"], 1)
        self.assertAlmostEqual(existing.rate, 1 / 777, places=8)
        self.assertEqual(self.config.last_sync_date, cursor)
        self.assertEqual(self.config.last_sync_status, "partial")

    def test_rates_are_isolated_to_active_company_root(self):
        other_company = self.env["res.company"].create(
            {
                "name": "Bcentral range isolated",
                "country_id": self.env.ref("base.cl").id,
            }
        )
        other_config = other_company._get_l10n_cl_bcentral_config()
        rate_date = date(2000, 5, 10)
        with patch.object(
            type(other_config),
            "_fetch_source_records",
            return_value=[{"date": rate_date, "value": 905, "status": "ok"}],
        ):
            result = other_config.action_fetch_currency_range(
                self.currency_usd, rate_date, rate_date
            )
        Rate = self.env["res.currency.rate"].sudo()
        self.assertEqual(result["created"], 1)
        self.assertTrue(
            Rate.search(self._rate_domain(self.currency_usd, rate_date, other_company))
        )
        self.assertFalse(Rate.search(self._rate_domain(self.currency_usd, rate_date)))

    def test_bcentral_provider_is_selected_without_fallback(self):
        token_param = "l10n_cl_currency_rate_bcentral.bcentral_api_token"
        self.env["ir.config_parameter"].sudo().set_param(token_param, "test-token")
        rate_date = date(2000, 5, 8)
        with patch(
            "odoo.addons.l10n_cl_currency_rate_bcentral.services.bcentral_api.BcentralApi.fetch_range",
            return_value=[{"date": rate_date, "value": 904, "status": "ok"}],
        ) as bde_fetch:
            result = self.config.action_fetch_currency_range(
                self.currency_usd, rate_date, rate_date
            )
        bde_fetch.assert_called_once_with("USD", rate_date, rate_date, "test-token")
        self.assertEqual(result["created"], 1)

    def test_utm_month_overlap_fetches_one_effective_observation_per_month(self):
        start, end = date(2000, 5, 15), date(2000, 6, 2)
        records = [
            {"date": date(2000, 5, 1), "value": 69000, "status": "ok"},
            {"date": date(2000, 6, 1), "value": 69200, "status": "ok"},
            {"date": date(2000, 7, 1), "value": 69400, "status": "ok"},
        ]
        with patch.object(
            type(self.config), "_fetch_source_records", return_value=records
        ) as fetch:
            result = self.config.action_fetch_currency_range(
                self.currency_utm, start, end
            )
        fetch.assert_called_once_with("UTM", date(2000, 5, 1), end)
        self.assertEqual(result["created"], 2)
        Rate = self.env["res.currency.rate"].sudo()
        self.assertTrue(
            Rate.search(self._rate_domain(self.currency_utm, date(2000, 5, 1)))
        )
        self.assertTrue(
            Rate.search(self._rate_domain(self.currency_utm, date(2000, 6, 1)))
        )
        self.assertFalse(
            Rate.search(self._rate_domain(self.currency_utm, date(2000, 6, 2)))
        )

    def test_empty_publication_and_provider_failure_are_reported(self):
        rate_date = date(2000, 5, 9)
        with patch.object(type(self.config), "_fetch_source_records", return_value=[]):
            empty = self.config.action_fetch_currency_range(
                self.currency_usd, rate_date, rate_date
            )
        self.assertEqual(empty["no_publication"], 1)
        with patch.object(
            type(self.config),
            "_fetch_source_records",
            side_effect=ValueError("Provider unavailable"),
        ):
            failed = self.config.action_fetch_currency_range(
                self.currency_usd, rate_date, rate_date
            )
        self.assertEqual(failed["failed"], 1)
        self.assertEqual(failed["errors"], ["Provider request failed (ValueError)"])

    def test_invalid_provider_observation_is_counted_as_partial_failure(self):
        rate_date = date(2000, 5, 11)
        with patch.object(
            type(self.config),
            "_fetch_source_records",
            return_value=[{"date": rate_date, "value": "bad", "status": "ok"}],
        ):
            result = self.config.action_fetch_currency_range(
                self.currency_usd, rate_date, rate_date
            )
        self.assertEqual(result["failed"], 1)
        self.assertIn("ValidationError", result["errors"][0])
        self.assertFalse(
            self.env["res.currency.rate"]
            .sudo()
            .search(self._rate_domain(self.currency_usd, rate_date))
        )

    def test_malformed_provider_record_is_reported_as_failure(self):
        rate_date = date(2000, 5, 12)
        with patch.object(
            type(self.config), "_fetch_source_records", return_value=[{}]
        ):
            result = self.config.action_fetch_currency_range(
                self.currency_usd, rate_date, rate_date
            )
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["no_publication"], 0)
