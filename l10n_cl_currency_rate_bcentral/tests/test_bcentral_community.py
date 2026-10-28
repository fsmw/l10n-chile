# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from datetime import date, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from ..models.bcentral_config import migrate_legacy_currency_selection
from ..models.res_company import SUPPORTED_CURRENCY_CODES


@tagged("post_install", "-at_install", "l10n_cl_currency_rate_bcentral")
class TestBcentralCommunityIntegration(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.chile = cls.env.ref("base.cl")
        cls.foreign_country = cls.env.ref("base.us")
        cls.company.country_id = cls.chile
        cls.config = cls.company._get_l10n_cl_bcentral_config()

    def _disable_other_companies(self):
        configs = self.env["l10n.cl.bcentral.config"].search(
            [("company_id", "!=", self.company.id)]
        )
        configs.write({"active": False})
        self.company.l10n_cl_bcentral_enabled = True
        self.config = self.company._get_l10n_cl_bcentral_config()
        self.config.write({"active": True})

    def test_inactive_config_is_reused(self):
        self.config.active = False
        self.assertEqual(
            self.company._get_l10n_cl_bcentral_config(),
            self.config,
        )

    def test_company_settings_are_persistent_and_company_scoped(self):
        self.company.write(
            {
                "l10n_cl_bcentral_enabled": False,
                "l10n_cl_bcentral_interval": "weekly",
                "l10n_cl_bcentral_sync_eur": True,
                "l10n_cl_bcentral_sync_utm": False,
            }
        )
        settings = self.env["res.config.settings"].create(
            {"company_id": self.company.id}
        )
        self.assertTrue(settings.l10n_cl_bcentral_is_chilean)
        self.assertFalse(settings.l10n_cl_bcentral_enabled)
        self.assertEqual(settings.l10n_cl_bcentral_interval, "weekly")
        self.assertFalse(settings.l10n_cl_bcentral_sync_utm)
        self.assertEqual(
            set(settings.l10n_cl_bcentral_currency_ids.mapped("name")),
            {"UF", "USD", "EUR"},
        )

        other = self.env["res.company"].create(
            {"name": "Bcentral Chile 2", "country_id": self.chile.id}
        )
        self.assertFalse(other.l10n_cl_bcentral_enabled)
        self.assertEqual(other.l10n_cl_bcentral_interval, "daily")
        other.write(
            {
                "l10n_cl_bcentral_enabled": True,
                "l10n_cl_bcentral_interval": "monthly",
            }
        )
        self.assertEqual(self.company.l10n_cl_bcentral_interval, "weekly")
        self.assertEqual(other.l10n_cl_bcentral_interval, "monthly")
        self.assertFalse(self.company.l10n_cl_bcentral_enabled)
        self.assertTrue(other.l10n_cl_bcentral_enabled)

    def test_non_chilean_activation_is_rejected(self):
        other = self.env["res.company"].create(
            {"name": "Bcentral Foreign", "country_id": self.foreign_country.id}
        )
        with self.assertRaises(ValidationError):
            other.write({"l10n_cl_bcentral_enabled": True})

        # The company constraint is the server-side guard even when the
        # accounting settings block is hidden for this company.
        self.assertNotEqual(other.country_id.code, "CL")

    def test_currency_selection_is_company_scoped_and_rejects_unsupported(self):
        currencies = self.env["res.currency"].with_context(active_test=False)
        supported = {
            currency.name: currency
            for currency in currencies.search(
                [("name", "in", SUPPORTED_CURRENCY_CODES)]
            )
        }
        self.company.write(
            {
                "l10n_cl_bcentral_currency_ids": [
                    (6, 0, [supported["UF"].id, supported["EUR"].id])
                ]
            }
        )
        self.assertEqual(
            set(self.company.l10n_cl_bcentral_currency_ids.mapped("name")),
            {"UF", "EUR"},
        )
        other = self.env["res.company"].create(
            {"name": "Bcentral Isolated", "country_id": self.chile.id}
        )
        self.assertEqual(
            set(other.l10n_cl_bcentral_currency_ids.mapped("name")),
            set(SUPPORTED_CURRENCY_CODES),
        )
        unsupported = currencies.search(
            [("name", "not in", SUPPORTED_CURRENCY_CODES)], limit=1
        )
        with self.assertRaises(ValidationError):
            self.company.write(
                {"l10n_cl_bcentral_currency_ids": [(6, 0, unsupported.ids)]}
            )

    def test_legacy_configuration_is_synchronized_without_deleting_logs(self):
        self.config.with_context(skip_bcentral_company_sync=True).write(
            {
                "active": True,
                "sync_uf": False,
                "sync_usd": True,
                "sync_eur": False,
                "sync_utm": True,
                "last_sync_date": date(2026, 5, 29),
                "last_sync_status": "partial",
                "last_sync_error": "EUR API down",
            }
        )
        log = self.env["l10n.cl.bcentral.log"].create(
            {
                "config_id": self.config.id,
                "status": "success",
                "rate_value": 900,
            }
        )
        self.config._sync_company_from_legacy()
        self.assertTrue(self.company.l10n_cl_bcentral_enabled)
        self.assertFalse(self.company.l10n_cl_bcentral_sync_uf)
        self.assertTrue(self.company.l10n_cl_bcentral_sync_utm)
        self.assertEqual(
            set(self.company.l10n_cl_bcentral_currency_ids.mapped("name")),
            {"USD", "UTM"},
        )
        self.assertEqual(self.config.last_sync_date, date(2026, 5, 29))
        self.assertEqual(self.config.last_sync_status, "partial")
        self.assertEqual(self.config.last_sync_error, "EUR API down")
        self.assertTrue(log.exists())

    def test_settings_view_is_community_accounting_view_and_has_utm(self):
        view = self.env.ref(
            "l10n_cl_currency_rate_bcentral.res_config_settings_view_bcentral"
        )
        self.assertEqual(
            view.inherit_id, self.env.ref("account.res_config_settings_view_form")
        )
        for field_name in (
            "l10n_cl_bcentral_enabled",
            "l10n_cl_bcentral_currency_ids",
            "l10n_cl_bcentral_interval",
            "l10n_cl_bcentral_next_run",
        ):
            self.assertIn(field_name, view.arch_db)
        self.assertIn("many2many_tags", view.arch_db)
        self.assertIn("no_create", view.arch_db)
        self.assertIn("no_create_edit", view.arch_db)
        for field_name in (
            "l10n_cl_bcentral_sync_uf",
            "l10n_cl_bcentral_sync_usd",
            "l10n_cl_bcentral_sync_eur",
            "l10n_cl_bcentral_sync_utm",
        ):
            self.assertNotIn(f'name="{field_name}"', view.arch_db)

        advanced_view = self.env.ref(
            "l10n_cl_currency_rate_bcentral.view_l10n_cl_bcentral_config_form"
        )
        self.assertIn('name="sync_utm"', advanced_view.arch_db)

    def test_all_supported_currencies_are_mapped_and_configurable(self):
        self.company.write(
            {
                "l10n_cl_bcentral_sync_uf": True,
                "l10n_cl_bcentral_sync_usd": True,
                "l10n_cl_bcentral_sync_eur": True,
                "l10n_cl_bcentral_sync_utm": True,
            }
        )
        config = self.company._get_l10n_cl_bcentral_config()
        currencies = config._get_currency_map()
        self.assertEqual(list(currencies), ["UF", "USD", "EUR", "UTM"])

    def test_legacy_selection_migration_preserves_empty_and_partial_sets(self):
        self.config.with_context(skip_bcentral_company_sync=True).write(
            {"sync_uf": True, "sync_usd": False, "sync_eur": True, "sync_utm": False}
        )
        self.company.l10n_cl_bcentral_currency_ids = [(5, 0, 0)]
        migrate_legacy_currency_selection(self.env)
        self.assertEqual(
            set(self.company.l10n_cl_bcentral_currency_ids.mapped("name")),
            {"UF", "EUR"},
        )
        first_ids = self.company.l10n_cl_bcentral_currency_ids.ids
        migrate_legacy_currency_selection(self.env)
        self.assertEqual(self.company.l10n_cl_bcentral_currency_ids.ids, first_ids)
        self.config.with_context(skip_bcentral_company_sync=True).write(
            {"sync_uf": False, "sync_usd": False, "sync_eur": False, "sync_utm": False}
        )
        self.company.l10n_cl_bcentral_currency_ids = [(5, 0, 0)]
        migrate_legacy_currency_selection(self.env)
        self.assertFalse(self.company.l10n_cl_bcentral_currency_ids)

    def test_cron_respects_interval_and_advances_only_after_success(self):
        self._disable_other_companies()
        now = fields.Datetime.now()
        self.company.write(
            {
                "l10n_cl_bcentral_enabled": True,
                "l10n_cl_bcentral_interval": "daily",
                "l10n_cl_bcentral_next_run": now - timedelta(minutes=1),
            }
        )
        self.config.write(
            {
                "last_sync_date": fields.Date.today(),
            }
        )
        self.env["l10n.cl.bcentral.config"].action_sync_daily_rates()
        self.assertGreater(self.company.l10n_cl_bcentral_next_run, now)

        past = fields.Datetime.now() - timedelta(minutes=1)
        self.company.l10n_cl_bcentral_next_run = past
        self.config.write(
            {
                "last_sync_date": False,
            }
        )
        with patch.object(type(self.config), "_catch_up_pending", return_value=False):
            self.env["l10n.cl.bcentral.config"].action_sync_daily_rates()
        self.assertEqual(self.company.l10n_cl_bcentral_next_run, past)

        self.company.write(
            {
                "l10n_cl_bcentral_interval": "manual",
                "l10n_cl_bcentral_next_run": past,
            }
        )
        with patch.object(type(self.config), "_catch_up_pending") as catch_up:
            self.env["l10n.cl.bcentral.config"].action_sync_daily_rates()
        catch_up.assert_not_called()

    def test_manual_sync_reports_failure_instead_of_success(self):
        with patch.object(type(self.config), "_sync_date", return_value=False):
            action = self.config.action_sync_manual()
        self.assertEqual(action["params"]["type"], "warning")
        self.assertTrue(action["params"]["sticky"])
        self.assertIn("errors", action["params"]["message"])

    def test_cron_is_single_and_stable(self):
        cron = self.env.ref("l10n_cl_currency_rate_bcentral.ir_cron_bcentral_sync")
        duplicate = self.env["ir.cron"].create(
            {
                "name": cron.name,
                "model_id": cron.model_id.id,
                "state": "code",
                "code": "model.action_sync_daily_rates()",
                "interval_number": 1,
                "interval_type": "days",
                "user_id": self.env.user.id,
            }
        )
        cron.active = False
        self.config._ensure_single_cron()
        self.assertFalse(duplicate.exists())
        self.assertFalse(cron.active)
        self.assertTrue(cron.exists())
        self.assertEqual(
            self.env["ir.cron"]
            .with_context(active_test=False)
            .search_count([("id", "=", cron.id)]),
            1,
        )
