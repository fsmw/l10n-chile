# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import json
import urllib.error
from datetime import date, datetime, timedelta
from email.message import Message
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase

from ..services.bcentral_api import BcentralApi


class TestBcentralSync(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.country_id = cls.env.ref("base.cl")
        cls.company.currency_id = cls.env.ref("base.CLP")
        cls.config = (
            cls.env["l10n.cl.bcentral.config"]
            .with_context(active_test=False)
            .search(
                [
                    ("company_id", "=", cls.company.id),
                ],
                limit=1,
            )
        )
        if not cls.config:
            cls.config = cls.env["l10n.cl.bcentral.config"].create(
                {
                    "company_id": cls.company.id,
                }
            )
        cls.config.write({"sync_uf": True, "sync_usd": True, "sync_eur": True})
        # Ensure currencies exist
        cls.currency_usd = cls.env["res.currency"].search(
            [("name", "=", "USD")], limit=1
        )
        cls.currency_eur = cls.env["res.currency"].search(
            [("name", "=", "EUR")], limit=1
        )
        cls.currency_utm = cls.env["res.currency"].search(
            [("name", "=", "UTM")], limit=1
        )
        cls.currency_uf = cls.env["res.currency"].search([("name", "=", "UF")], limit=1)
        if not cls.currency_uf:
            cls.currency_uf = cls.env["res.currency"].create(
                {
                    "name": "UF",
                    "symbol": "UF",
                    "rounding": 0.01,
                    "decimal_places": 2,
                }
            )

    def test_01_config_unique_company(self):
        """Only one config per company is allowed."""
        with self.env.cr.savepoint(), self.assertRaises(IntegrityError):
            self.env["l10n.cl.bcentral.config"].create(
                {"company_id": self.company.id}
            ).flush_recordset()

    def test_02_get_currency_map(self):
        """Currency map returns enabled currencies."""
        currencies = self.config._get_currency_map()
        self.assertIn("USD", currencies)
        self.assertIn("EUR", currencies)
        self.assertIn("UF", currencies)

    def test_bcentral_series_are_mapped_to_official_codes(self):
        self.assertEqual(BcentralApi.SERIES_MAP["UF"], "F073.UFF.PRE.Z.D")
        self.assertEqual(BcentralApi.SERIES_MAP["USD"], "F073.TCO.PRE.Z.D")
        self.assertEqual(BcentralApi.SERIES_MAP["EUR"], "F072.CLP.EUR.N.O.D")
        self.assertEqual(BcentralApi.SERIES_MAP["UTM"], "F073.UTR.PRE.Z.M")

    def test_bcentral_fetch_normalizes_daily_observations(self):
        with patch.object(
            BcentralApi,
            "_fetch",
            return_value=[
                {
                    "indexDateString": "21-10-2026",
                    "value": "978.56",
                    "statusCode": "OK",
                },
                {
                    "indexDateString": "22-10-2026",
                    "value": "NaN",
                    "statusCode": "ND",
                },
            ],
        ) as fetch:
            records = BcentralApi.fetch_range(
                "USD", date(2026, 10, 21), date(2026, 10, 22), "token-for-test"
            )
        fetch.assert_called_once_with(
            "token-for-test",
            "F073.TCO.PRE.Z.D",
            date(2026, 10, 21),
            date(2026, 10, 22),
        )
        self.assertEqual(
            records,
            [
                {"date": date(2026, 10, 21), "value": 978.56, "status": "ok"},
                {"date": date(2026, 10, 22), "value": None, "status": "no_data"},
            ],
        )

    def test_bcentral_fetch_decodes_legacy_response_charset(self):
        secret = "bde-private-token"
        series_id = BcentralApi.SERIES_MAP["USD"]
        payload = {
            "Codigo": 0,
            "Series": {
                "seriesId": series_id,
                "Descripcion": "Dólar observado",
                "Obs": [],
            },
        }
        response = MagicMock()
        response.headers = Message()
        response.headers["Content-Type"] = "application/json"
        response.read.return_value = json.dumps(payload, ensure_ascii=False).encode(
            "cp1252"
        )
        response.__enter__.return_value = response

        with patch(
            "odoo.addons.l10n_cl_currency_rate_bcentral.services.bcentral_api.urllib.request.urlopen",
            return_value=response,
        ):
            observations = BcentralApi._fetch(
                secret,
                series_id,
                date(2026, 10, 1),
                date(2026, 10, 2),
            )

        self.assertEqual(observations, [])

    def test_bcentral_utm_request_starts_on_month_effective_date(self):
        with patch.object(BcentralApi, "_fetch", return_value=[]) as fetch:
            BcentralApi.fetch_range(
                "UTM", date(2026, 10, 21), date(2026, 10, 21), "token-for-test"
            )
        fetch.assert_called_once_with(
            "token-for-test",
            "F073.UTR.PRE.Z.M",
            date(2026, 10, 1),
            date(2026, 10, 21),
        )

    def test_bcentral_auth_errors_do_not_expose_token(self):
        secret = "bde-private-token"
        error = urllib.error.HTTPError(
            "https://example.invalid", 401, "Denied", {}, None
        )
        with patch(
            "odoo.addons.l10n_cl_currency_rate_bcentral.services.bcentral_api.urllib.request.urlopen",
            side_effect=error,
        ):
            with self.assertRaises(ValueError) as raised:
                BcentralApi._fetch(
                    secret,
                    BcentralApi.SERIES_MAP["USD"],
                    date(2026, 10, 1),
                    date(2026, 10, 2),
                )
        self.assertNotIn(secret, str(raised.exception))
        self.assertIn("HTTP 401", str(raised.exception))

    def test_bcentral_catch_up_is_idempotent_and_preserves_existing_values(self):
        today = fields.Date.today()
        existing_date = today - timedelta(days=2)
        new_date = today - timedelta(days=1)
        self.company.l10n_cl_bcentral_currency_ids = [(6, 0, [self.currency_usd.id])]
        self.config.write({"last_sync_date": False})
        existing = (
            self.env["res.currency.rate"]
            .sudo()
            .search(
                [
                    ("name", "=", existing_date),
                    ("currency_id", "=", self.currency_usd.id),
                    ("company_id", "=", self.company.root_id.id),
                ],
                limit=1,
            )
        )
        if existing:
            existing.inverse_company_rate = 900.0
        else:
            existing = (
                self.env["res.currency.rate"]
                .sudo()
                .create(
                    {
                        "name": existing_date,
                        "currency_id": self.currency_usd.id,
                        "company_id": self.company.root_id.id,
                        "inverse_company_rate": 900.0,
                    }
                )
            )
        observations = [
            {"date": existing_date, "value": 901.0, "status": "ok"},
            {"date": new_date, "value": 902.0, "status": "ok"},
            {"date": today, "value": 903.0, "status": "ok"},
        ]
        with patch.object(
            type(self.config), "_fetch_source_records", return_value=observations
        ):
            self.assertTrue(self.config._catch_up_pending())
            self.assertTrue(self.config._catch_up_pending())

        existing.invalidate_recordset()
        self.assertEqual(existing.inverse_company_rate, 900.0)
        new_rate_count = (
            self.env["res.currency.rate"]
            .sudo()
            .search_count(
                [
                    ("name", "=", new_date),
                    ("currency_id", "=", self.currency_usd.id),
                    ("company_id", "=", self.company.root_id.id),
                ]
            )
        )
        self.assertEqual(new_rate_count, 1)
        self.assertEqual(self.config.last_sync_date, today)

    def test_bcentral_catch_up_failure_retains_cursor(self):
        cursor = fields.Date.today() - timedelta(days=2)
        self.company.l10n_cl_bcentral_currency_ids = [(6, 0, [self.currency_usd.id])]
        self.config.write(
            {
                "last_sync_date": cursor,
                "sync_usd": True,
                "sync_uf": False,
                "sync_eur": False,
                "sync_utm": False,
            }
        )
        with patch.object(
            type(self.config),
            "_fetch_source_records",
            side_effect=ValueError("Banco Central connection error"),
        ):
            self.assertFalse(self.config._catch_up_pending())

        self.assertEqual(self.config.last_sync_date, cursor)
        self.assertEqual(self.config.last_sync_status, "partial")
        self.assertIn("USD", self.config.last_sync_error)

    def test_morning_schedule_uses_santiago_timezone_and_dst(self):
        santiago = ZoneInfo("America/Santiago")
        self.assertEqual(
            self.config._to_odoo_utc(datetime(2026, 10, 22, 5, 30, tzinfo=santiago)),
            datetime(2026, 10, 22, 8, 30),
        )
        self.assertEqual(
            self.config._to_odoo_utc(datetime(2026, 6, 22, 5, 30, tzinfo=santiago)),
            datetime(2026, 6, 22, 9, 30),
        )

    def test_pending_retry_slots_and_cutoff(self):
        santiago = ZoneInfo("America/Santiago")
        cases = [
            (
                datetime(2026, 10, 22, 5, 10, tzinfo=santiago),
                datetime(2026, 10, 22, 8, 30),
            ),
            (
                datetime(2026, 10, 22, 5, 45, tzinfo=santiago),
                datetime(2026, 10, 22, 9, 30),
            ),
            (
                datetime(2026, 10, 22, 8, 35, tzinfo=santiago),
                datetime(2026, 10, 23, 8, 30),
            ),
            (
                datetime(2026, 10, 22, 16, 0, tzinfo=santiago),
                datetime(2026, 10, 23, 8, 30),
            ),
        ]
        for now, expected in cases:
            with (
                self.subTest(now=now),
                patch.object(type(self.config), "_santiago_now", return_value=now),
            ):
                self.assertEqual(self.config._next_daily_slot(pending=True), expected)

    def test_catch_up_retries_only_pending_daily_currency_and_recovers_late_rate(self):
        today = date(2026, 10, 22)
        yesterday = today - timedelta(days=1)
        self.company.l10n_cl_bcentral_currency_ids = [
            (6, 0, [self.currency_usd.id, self.currency_uf.id])
        ]
        self.config.write(
            {
                "last_sync_date": yesterday,
                "last_sync_attempt": False,
                "sync_utm": False,
            }
        )
        self.config._create_rate_if_missing(self.currency_usd, today, 976.82)
        with (
            patch.object(type(self.config), "_santiago_today", return_value=today),
            patch.object(
                type(self.config), "_fetch_source_records", return_value=[]
            ) as empty_fetch,
        ):
            self.assertTrue(self.config._catch_up_pending())
            empty_fetch.assert_called_once_with("UF", today, today)
            self.assertEqual(self.config.last_sync_date, yesterday)
            self.assertEqual(self.config.last_sync_status, "partial")

            self.company.l10n_cl_bcentral_currency_ids = [
                (
                    6,
                    0,
                    [self.currency_usd.id, self.currency_uf.id, self.currency_utm.id],
                )
            ]
            self.config.sync_utm = False
            with patch.object(
                type(self.config),
                "_fetch_source_records",
                return_value=[{"date": yesterday, "value": 41100.0, "status": "ok"}],
            ) as stale_fetch:
                self.assertTrue(self.config._catch_up_pending())
                stale_fetch.assert_called_once_with("UF", today, today)
            self.assertEqual(self.config.last_sync_date, yesterday)

            with patch.object(
                type(self.config),
                "_fetch_source_records",
                return_value=[{"date": today, "value": 41200.0, "status": "ok"}],
            ) as late_fetch:
                self.assertTrue(self.config._catch_up_pending())
                late_fetch.assert_called_once_with("UF", today, today)

        self.assertEqual(self.config.last_sync_date, today)
        with patch.object(type(self.config), "_santiago_today", return_value=today):
            self.assertFalse(self.config._has_pending_daily_rates())
        rate = (
            self.env["res.currency.rate"]
            .sudo()
            .search(
                [
                    ("currency_id", "=", self.currency_uf.id),
                    ("name", "=", today),
                    ("company_id", "=", self.company.root_id.id),
                ],
                limit=1,
            )
        )
        self.assertAlmostEqual(rate.inverse_company_rate, 41200.0)

    def test_cron_runs_hourly_and_manual_only_company_is_not_due(self):
        cron = self.env.ref("l10n_cl_currency_rate_bcentral.ir_cron_bcentral_sync")
        self.assertEqual(cron.interval_number, 1)
        self.assertEqual(cron.interval_type, "hours")
        self.company.l10n_cl_bcentral_interval = "manual"
        self.assertFalse(self.config._is_due())

    def test_02b_currency_processing_order_is_canonical(self):
        currencies = (
            self.env["res.currency"]
            .with_context(active_test=False)
            .search([("name", "in", ["UF", "USD", "EUR", "UTM"])])
        )
        self.company.l10n_cl_bcentral_currency_ids = [(6, 0, currencies.ids[::-1])]
        seen = []

        def fetch_currency(code, _start, _end):
            seen.append(code)
            return []

        with patch.object(
            type(self.config), "_fetch_source_records", side_effect=fetch_currency
        ):
            self.assertTrue(self.config._sync_date(fields.Date.today()))
        self.assertEqual(seen, ["UF", "USD", "EUR", "UTM"])

    def test_02c_backfill_uses_only_selected_currency(self):
        self.company.l10n_cl_bcentral_currency_ids = [(6, 0, [self.currency_usd.id])]
        with patch.object(
            type(self.config), "_fetch_source_records", return_value=[]
        ) as fetch_records:
            self.config._backfill_current_year()
        fetch_records.assert_called_once()
        self.assertEqual(fetch_records.call_args.args[0], "USD")

    def test_03_disable_currency_excluded(self):
        """Disabled currencies are not returned."""
        self.config.sync_uf = False
        currencies = self.config._get_currency_map()
        self.assertNotIn("UF", currencies)
        self.config.sync_uf = True

    def test_04_upsert_rate(self):
        """Rate can be created and updated."""

        self.config._upsert_rate(self.currency_usd, date(2026, 5, 12), 894.25)
        rate = self.env["res.currency.rate"].search(
            [
                ("currency_id", "=", self.currency_usd.id),
                ("name", "=", date(2026, 5, 12)),
                ("company_id", "=", self.company.root_id.id),
            ],
            limit=1,
        )
        self.assertTrue(rate)
        self.assertAlmostEqual(rate.inverse_company_rate, 894.25, places=2)
        self.assertAlmostEqual(rate.rate, 1 / 894.25, places=8)

        # Update same date
        self.config._upsert_rate(self.currency_usd, date(2026, 5, 12), 900.00)
        rate.invalidate_recordset()
        self.assertAlmostEqual(rate.inverse_company_rate, 900.00, places=2)

    def test_04b_real_usd_to_clp_conversion(self):
        """USD 12,300 uses the published CLP-per-USD value."""

        rate_date = date(2026, 5, 29)
        self.config._upsert_rate(self.currency_usd, rate_date, 892.89)
        amount = self.currency_usd._convert(
            12300, self.company.currency_id, self.company, rate_date
        )
        self.assertEqual(amount, 10982547.0)

    def test_04c_invalid_rate_rejected(self):
        with self.assertRaises(ValidationError):
            self.config._upsert_rate(self.currency_usd, date(2026, 5, 29), 0)

    def test_04d_future_rate_rejected(self):
        with self.assertRaises(ValidationError):
            self.config._upsert_rate(
                self.currency_usd,
                fields.Date.today() + timedelta(days=1),
                900.00,
            )

    def test_04e_source_rate_normalization(self):
        self.assertEqual(self.config._normalize_source_rate("892.89"), 892.89)
        with self.assertRaises(ValidationError):
            self.config._normalize_source_rate(float("nan"))

    def test_05_sync_date_success(self):
        """Successful daily sync updates last_sync_date."""

        records = [{"date": "2026-05-12", "value": 894.25, "status": "ok"}]
        with patch.object(
            type(self.config), "_fetch_source_records", return_value=records
        ):
            result = self.config._sync_date(date(2026, 5, 12))
            self.assertTrue(result)
            self.assertEqual(self.config.last_sync_date, date(2026, 5, 12))
            self.assertEqual(self.config.last_sync_status, "success")

    def test_06_sync_date_failure_retries(self):
        """Failed sync does not advance last_sync_date."""

        self.config.last_sync_date = False
        with patch.object(
            type(self.config),
            "_fetch_source_records",
            side_effect=Exception("API down"),
        ):
            result = self.config._sync_date(date(2026, 5, 12))
            self.assertFalse(result)
            self.assertNotEqual(self.config.last_sync_date, date(2026, 5, 12))
            self.assertEqual(self.config.last_sync_status, "failed")

    def test_06b_initial_non_publication_advances_cursor(self):
        """A confirmed empty publication response must not deadlock the cron."""

        self.config.write(
            {
                "last_sync_date": False,
                "sync_uf": False,
                "sync_eur": True,
                "sync_usd": True,
                "sync_utm": False,
            }
        )
        target = date(2026, 1, 1)
        self.env["res.currency.rate"].sudo().search(
            [
                ("name", "<=", target),
                ("currency_id", "in", [self.currency_usd.id, self.currency_eur.id]),
                ("company_id", "=", self.company.root_id.id),
            ]
        ).unlink()
        with patch.object(type(self.config), "_fetch_source_records", return_value=[]):
            self.assertTrue(self.config._sync_date(target))

        self.assertEqual(self.config.last_sync_date, target)
        self.assertEqual(self.config.last_sync_status, "success")
        self.assertFalse(
            self.env["res.currency.rate"].search(
                [
                    ("name", "=", target),
                    ("currency_id", "in", [self.currency_usd.id, self.currency_eur.id]),
                    ("company_id", "=", self.company.root_id.id),
                ]
            )
        )

    def test_06c_source_failure_retains_cursor_and_is_visible(self):
        """A source failure must block advancement and expose diagnostics."""

        self.config.write(
            {
                "last_sync_date": False,
                "sync_uf": False,
                "sync_eur": False,
                "sync_usd": True,
                "sync_utm": False,
            }
        )
        with patch.object(
            type(self.config),
            "_fetch_source_records",
            side_effect=Exception("API down"),
        ):
            self.assertFalse(self.config._sync_date(date(2026, 1, 1)))

        self.assertFalse(self.config.last_sync_date)
        self.assertEqual(self.config.last_sync_status, "failed")
        self.assertIn("API down", self.config.last_sync_error)
        self.assertTrue(self.config.last_sync_error_date)

    def test_06d_mixed_skip_and_failure_retains_cursor(self):
        """A skipped currency must not hide a failure in another currency."""

        self.config.write(
            {
                "last_sync_date": False,
                "sync_uf": False,
                "sync_eur": True,
                "sync_usd": True,
                "sync_utm": False,
            }
        )

        def fetch_currency(code, _start, _end):
            if code == "USD":
                return []
            raise Exception("EUR API down")

        with patch.object(
            type(self.config), "_fetch_source_records", side_effect=fetch_currency
        ):
            self.assertFalse(self.config._sync_date(date(2026, 1, 1)))

        self.assertFalse(self.config.last_sync_date)
        self.assertEqual(self.config.last_sync_status, "failed")
        self.assertIn("EUR API down", self.config.last_sync_error)

    def test_07_log_created(self):
        """Sync creates log entries."""

        self.config._log_sync(date(2026, 5, 12), self.currency_usd, 894.25, "success")
        log = self.env["l10n.cl.bcentral.log"].search(
            [
                ("config_id", "=", self.config.id),
                ("sync_date", "=", date(2026, 5, 12)),
            ],
            limit=1,
        )
        self.assertTrue(log)
        self.assertEqual(log.status, "success")
        self.assertAlmostEqual(log.rate_value, 894.25, places=2)

    def test_07b_weekend_with_prior_rate_advances(self):
        """A non-publishing day is covered by the last prior official rate."""

        self.config.sync_uf = False
        self.config.sync_eur = False
        self.config.sync_utm = False
        self.config._upsert_rate(self.currency_usd, date(2026, 5, 29), 892.89)
        with patch.object(type(self.config), "_fetch_source_records", return_value=[]):
            self.assertTrue(self.config._sync_date(date(2026, 5, 30)))
        self.assertEqual(self.config.last_sync_date, date(2026, 5, 30))

    def test_07c_repair_uses_source_value_not_rate(self):
        """Reconciliation repairs from source values, never from rate."""

        rate_date = date(2026, 5, 29)
        Rate = self.env["res.currency.rate"].sudo()
        existing = Rate.search(
            [
                ("name", "=", rate_date),
                ("currency_id", "=", self.currency_usd.id),
                ("company_id", "=", self.company.root_id.id),
            ],
            limit=1,
        )
        if not existing:
            existing = Rate.create(
                {
                    "name": rate_date,
                    "currency_id": self.currency_usd.id,
                    "company_id": self.company.root_id.id,
                    "inverse_company_rate": 964.32,
                }
            )
        else:
            existing.inverse_company_rate = 964.32
        self.config.write({"sync_uf": False, "sync_eur": False, "sync_utm": False})
        with patch.object(
            type(self.config),
            "_get_source_records",
            return_value=[{"date": "2026-05-29", "value": 892.89}],
        ):
            result = self.config.action_reconcile_rates()
        existing.invalidate_recordset()
        self.assertEqual(result["updated"], 1)
        self.assertAlmostEqual(existing.inverse_company_rate, 892.89, places=2)
        self.assertAlmostEqual(existing.rate, 1 / 892.89, places=8)

    def test_07d_reconciliation_is_idempotent_and_cleans_future(self):
        Rate = self.env["res.currency.rate"].sudo()
        future_date = fields.Date.today() + timedelta(days=1)
        future = Rate.search(
            [
                ("name", "=", future_date),
                ("currency_id", "=", self.currency_usd.id),
                ("company_id", "=", self.company.root_id.id),
            ],
            limit=1,
        )
        if not future:
            future = Rate.create(
                {
                    "name": future_date,
                    "currency_id": self.currency_usd.id,
                    "company_id": self.company.root_id.id,
                    "inverse_company_rate": 999.0,
                }
            )
        future_count = Rate.search_count(
            [
                ("name", ">", fields.Date.today()),
                ("currency_id", "=", self.currency_usd.id),
                ("company_id", "=", self.company.root_id.id),
            ]
        )
        self.config.write({"sync_uf": False, "sync_eur": False, "sync_utm": False})
        with patch.object(
            type(self.config),
            "_get_source_records",
            return_value=[{"date": "2026-05-29", "value": 892.89}],
        ):
            first = self.config.action_reconcile_rates(repair_future=True)
            second = self.config.action_reconcile_rates(repair_future=True)
        self.assertEqual(first["future_removed"], future_count)
        self.assertFalse(future.exists())
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["updated"], 0)

    def test_07e_future_last_sync_date_does_not_advance(self):
        self.config.last_sync_date = fields.Date.today() + timedelta(days=1)
        self.assertFalse(self.config._get_target_date())

    def test_09_backfill_current_year(self):
        """Backfill loads rates for multiple days."""
        records = [
            {"date": "2026-01-03", "value": 890.00, "status": "ok"},
            {"date": "2026-01-04", "value": 891.00, "status": "ok"},
        ]
        with patch.object(
            type(self.config), "_fetch_source_records", return_value=records
        ):
            self.config._backfill_current_year()
            rates = self.env["res.currency.rate"].search(
                [
                    ("currency_id", "=", self.currency_usd.id),
                    ("company_id", "=", self.company.root_id.id),
                ]
            )
            self.assertTrue(len(rates) >= 2)
