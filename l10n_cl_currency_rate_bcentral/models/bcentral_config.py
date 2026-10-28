# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import logging
import math
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from odoo import _, api, exceptions, fields, models

from ..services.bcentral_api import BcentralApi
from .res_company import (
    BCENTRAL_COMPANY_FIELDS,
    LEGACY_CURRENCY_FIELDS,
    SUPPORTED_CURRENCY_CODES,
)

_logger = logging.getLogger(__name__)
SANTIAGO_TZ = ZoneInfo("America/Santiago")


def migrate_legacy_currency_selection(env):
    """Project the old per-currency flags into the canonical company relation."""
    currency_model = env["res.currency"].with_context(active_test=False)
    currencies = currency_model.search([("name", "in", SUPPORTED_CURRENCY_CODES)])
    by_code = {currency.name: currency for currency in currencies}
    missing = set(SUPPORTED_CURRENCY_CODES) - set(by_code)
    if missing:
        _logger.warning(
            "Bcentral migration: supported currencies not found: %s",
            ", ".join(code for code in SUPPORTED_CURRENCY_CODES if code in missing),
        )

    config_model = env["l10n.cl.bcentral.config"].sudo().with_context(active_test=False)
    companies = env["res.company"].sudo().search([])
    migrated = 0
    for company in companies:
        config = config_model.search([("company_id", "=", company.id)], limit=1)
        source = config or company
        selected_ids = [
            by_code[code].id
            for field_name, code in LEGACY_CURRENCY_FIELDS.items()
            if source[field_name if config else f"l10n_cl_bcentral_{field_name}"]
            and code in by_code
        ]
        company.with_context(skip_bcentral_company_sync=True).write(
            {"l10n_cl_bcentral_currency_ids": [(6, 0, selected_ids)]}
        )
        company._sync_legacy_currency_fields()
        migrated += 1
    return migrated


class BcentralConfig(models.Model):
    _name = "l10n.cl.bcentral.config"
    _description = "Bcentral Sync Configuration"
    _rec_name = "company_id"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        index=True,
        default=lambda self: self.env.company,
    )
    active = fields.Boolean(default=True)
    sync_uf = fields.Boolean(default=True)
    sync_usd = fields.Boolean(default=True)
    sync_eur = fields.Boolean(default=True)
    sync_utm = fields.Boolean(default=True)
    base_currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.ref("base.CLP", raise_if_not_found=False),
        readonly=True,
    )
    last_sync_date = fields.Date(
        string="Last Successful Sync Date",
        help="Last date that was fully synced for all enabled currencies.",
    )
    last_sync_attempt = fields.Datetime(readonly=True)
    sync_pending = fields.Boolean(compute="_compute_sync_pending")
    last_sync_status = fields.Selection(
        [
            ("success", "Success"),
            ("partial", "Partial"),
            ("failed", "Failed"),
        ],
        readonly=True,
    )
    last_sync_error = fields.Text(string="Latest Sync Error", readonly=True)
    last_sync_error_date = fields.Datetime(
        string="Latest Sync Error Date", readonly=True
    )

    _sql_constraints = [
        (
            "unique_company",
            "unique (company_id)",
            "Only one configuration per company is allowed.",
        ),
    ]

    # ---------------------------------------------------------------
    # Compatibility and scheduling helpers
    # ---------------------------------------------------------------
    def _sync_from_company(self):
        """Refresh legacy fields from the authoritative company settings."""
        for config in self:
            values = {
                legacy: config.company_id[company_field]
                for legacy, company_field in BCENTRAL_COMPANY_FIELDS.items()
            }
            config.with_context(skip_bcentral_company_sync=True).write(values)
        return self

    def _sync_company_from_legacy(self):
        """Migrate legacy operational values into company settings."""
        for config in self:
            values = {
                company_field: config[legacy]
                for legacy, company_field in BCENTRAL_COMPANY_FIELDS.items()
            }
            if config.company_id.country_id.code != "CL":
                values["l10n_cl_bcentral_enabled"] = False
            config.company_id.with_context(skip_bcentral_company_sync=True).write(
                values
            )
        return self

    @api.model
    def _ensure_single_cron(self):
        cron = self.env.ref(
            "l10n_cl_currency_rate_bcentral.ir_cron_bcentral_sync", False
        )
        if not cron:
            return False
        candidates = self.env["ir.cron"].search(
            [
                (
                    "name",
                    "in",
                    [
                        "Bcentral: Sync Daily Rates",
                        "Bcentral: Sync Rates and Catch Up",
                    ],
                ),
                ("model_id.model", "=", self._name),
            ]
        )
        (candidates - cron).unlink()
        cron.write(
            {
                "interval_number": 1,
                "interval_type": "hours",
                "code": "model.action_sync_daily_rates()",
            }
        )
        return cron

    def _ensure_chilean_company(self):
        self.ensure_one()
        if self.company_id.country_id.code != "CL":
            raise exceptions.ValidationError(
                _("Bcentral solo está disponible para compañías chilenas.")
            )

    def _is_due(self):
        self.ensure_one()
        if self.company_id.l10n_cl_bcentral_interval == "manual":
            return False
        next_run = self.company_id.l10n_cl_bcentral_next_run
        return not next_run or next_run <= fields.Datetime.now()

    @api.model
    def _santiago_now(self):
        """Return the current aware Santiago datetime (overridable in tests)."""
        return datetime.now(SANTIAGO_TZ)

    @api.model
    def _santiago_today(self):
        return self._santiago_now().date()

    @api.model
    def _to_odoo_utc(self, local_datetime):
        """Convert an aware local datetime to Odoo's naive UTC representation."""
        return local_datetime.astimezone(timezone.utc).replace(tzinfo=None)

    def _has_exact_rate_for_date(self, currency, target_date):
        self.ensure_one()
        return bool(
            self.env["res.currency.rate"]
            .sudo()
            .search_count(
                [
                    ("currency_id", "=", currency.id),
                    ("name", "=", target_date),
                    ("company_id", "=", self.company_id.root_id.id),
                ]
            )
        )

    def _next_daily_slot(self, pending=False):
        """Return 05:30 local, or the next hourly retry through 08:30."""
        self.ensure_one()
        now = self._santiago_now()
        first = datetime.combine(now.date(), time(5, 30), tzinfo=SANTIAGO_TZ)
        if pending:
            if now < first:
                return self._to_odoo_utc(first)
            elapsed_hours = int((now - first).total_seconds() // 3600) + 1
            candidate = first + timedelta(hours=elapsed_hours)
            cutoff = first.replace(hour=8, minute=30)
            if candidate <= cutoff:
                return self._to_odoo_utc(candidate)
        tomorrow = datetime.combine(
            now.date() + timedelta(days=1), time(5, 30), tzinfo=SANTIAGO_TZ
        )
        return self._to_odoo_utc(tomorrow)

    @api.depends("last_sync_date")
    def _compute_sync_pending(self):
        today = self._santiago_today()
        for config in self:
            config.sync_pending = (
                not config.last_sync_date or config.last_sync_date < today
            )

    def _advance_next_run(self):
        self.ensure_one()
        interval = self.company_id.l10n_cl_bcentral_interval
        if interval == "daily":
            next_run = self._next_daily_slot()
        elif interval == "weekly":
            next_run = fields.Datetime.add(fields.Datetime.now(), weeks=1)
        elif interval == "monthly":
            next_run = fields.Datetime.add(fields.Datetime.now(), months=1)
        else:
            return False
        self.company_id.sudo().write({"l10n_cl_bcentral_next_run": next_run})
        return next_run

    def _schedule_pending_retry(self):
        self.ensure_one()
        next_run = self._next_daily_slot(pending=True)
        self.company_id.sudo().write({"l10n_cl_bcentral_next_run": next_run})
        return next_run

    def write(self, vals):
        company_values = {
            BCENTRAL_COMPANY_FIELDS[field]: value
            for field, value in vals.items()
            if field in BCENTRAL_COMPANY_FIELDS
        }
        if company_values and not self.env.context.get("skip_bcentral_company_sync"):
            for config in self:
                config.company_id.with_context(skip_bcentral_company_sync=True).write(
                    company_values
                )
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        configs = super().create(vals_list)
        if not self.env.context.get("skip_bcentral_company_sync"):
            for config in configs:
                config.company_id.with_context(skip_bcentral_company_sync=True).write(
                    {
                        company_field: config[legacy]
                        for legacy, company_field in BCENTRAL_COMPANY_FIELDS.items()
                    }
                )
        return configs

    # ---------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------
    def _get_currency_map(self):
        """Return selected currencies in the canonical UF/USD/EUR/UTM order."""
        self.ensure_one()
        selected = {
            currency.name: currency
            for currency in self.company_id.l10n_cl_bcentral_currency_ids
        }
        return {
            code: selected[code]
            for code in SUPPORTED_CURRENCY_CODES
            if code in selected
        }

    def _has_pending_daily_rates(self):
        """Whether a selected daily series lacks an exact rate for Santiago today."""
        self.ensure_one()
        today = self._santiago_today()
        return any(
            code != "UTM" and not self._has_exact_rate_for_date(currency, today)
            for code, currency in self._get_currency_map().items()
        )

    def _get_target_date(self):
        """Determine the next non-future date that needs to be synced."""
        self.ensure_one()
        today = self._santiago_today()
        if not self.last_sync_date:
            target = date(self._get_current_year(), 1, 1)
            return target if target <= today else None
        target = self.last_sync_date + timedelta(days=1)
        return target if target <= today else None

    @api.model
    def _get_current_year(self):
        return self._santiago_today().year

    # ---------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------
    def action_sync_daily_rates(self):
        """Cron entry point: sync due Chilean companies."""
        for config in self.search([]):
            config._sync_from_company()
            if (
                not config.active
                or config.company_id.country_id.code != "CL"
                or not config._is_due()
            ):
                continue
            if (
                config.last_sync_date
                and config.last_sync_date >= config._santiago_today()
                and not config._has_pending_daily_rates()
            ):
                _logger.info(
                    "Bcentral: already up to date for company %s",
                    config.company_id.name,
                )
                config._advance_next_run()
                continue
            if config._catch_up_pending():
                if config._has_pending_daily_rates():
                    config._schedule_pending_retry()
                else:
                    config._advance_next_run()

    def action_sync_manual(self):
        """Button: sync today's rates immediately."""
        self.ensure_one()
        self._ensure_chilean_company()
        today = fields.Date.today()
        success = self._sync_date(today)
        if success:
            message = _("Rates synchronized successfully.")
        else:
            message = _("Synchronization completed with errors; check the latest log.")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Bcentral Sync"),
                "message": message,
                "type": "success" if success else "warning",
                "sticky": not success,
            },
        }

    def action_backfill_current_year(self):
        """Button: backfill from Jan 1st of current year up to today."""
        self.ensure_one()
        self._ensure_chilean_company()
        self._backfill_current_year()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Bcentral Backfill"),
                "message": _("Historical rates backfilled."),
                "type": "success",
                "sticky": False,
            },
        }

    # ---------------------------------------------------------------
    # Core sync logic
    # ---------------------------------------------------------------
    def _sync_date(self, target_date):
        """Sync rates for a specific date.

        Returns True if all currencies synced successfully, False otherwise.
        On partial failure last_sync_date is NOT updated so the cron retries.
        """
        self.ensure_one()
        self._ensure_chilean_company()
        target_date = fields.Date.to_date(target_date)
        if target_date > fields.Date.today():
            raise exceptions.ValidationError(
                _("Cannot synchronize a future exchange-rate date: %s") % target_date
            )
        currencies = self._get_currency_map()
        if not currencies:
            _logger.warning(
                "Bcentral: no currencies enabled for company %s", self.company_id.name
            )
            self.last_sync_date = target_date
            self.last_sync_status = "success"
            return True  # nothing to do, consider it done

        self.last_sync_attempt = fields.Datetime.now()
        all_ok = True
        for code, currency in currencies.items():
            try:
                records = self._fetch_source_records(code, target_date, target_date)
                records = [
                    record
                    for record in records
                    if record.get("status", "ok") == "ok"
                    and fields.Date.to_date(record["date"]) <= target_date
                ]
                record = max(
                    records,
                    key=lambda item: fields.Date.to_date(item["date"]),
                    default=None,
                )
                if not record:
                    if self._has_rate_coverage(currency, target_date):
                        _logger.info(
                            "Bcentral: no publication for %s on %s; using prior rate",
                            code,
                            target_date,
                        )
                        self._log_sync(
                            target_date,
                            currency,
                            None,
                            "skipped",
                            f"No publication for {code}; prior rate applies",
                        )
                    else:
                        _logger.warning(
                            "Bcentral: no data for %s on %s", code, target_date
                        )
                        self._log_sync(
                            target_date,
                            currency,
                            None,
                            "skipped",
                            f"No publication for {code} on {target_date}",
                        )
                    continue

                rate_date = fields.Date.to_date(record["date"])
                rate_value = self._normalize_source_rate(record.get("value"))
                self._upsert_rate(currency, rate_date, rate_value)
                self._log_sync(rate_date, currency, rate_value, "success")
            except Exception as exc:
                _logger.error(
                    "Bcentral: failed to sync %s on %s: %s", code, target_date, exc
                )
                all_ok = False
                self.last_sync_error = f"{code} on {target_date}: {exc}"
                self._log_sync(target_date, currency, None, "failed", str(exc))

        if all_ok:
            self.last_sync_date = target_date
            self.last_sync_status = "success"
            self.last_sync_error = False
            self.last_sync_error_date = False
            _logger.info(
                "Bcentral: successfully synced %s for company %s",
                target_date,
                self.company_id.name,
            )
        else:
            self.last_sync_status = "partial" if self.last_sync_date else "failed"
            self.last_sync_error_date = fields.Datetime.now()

        return all_ok

    def _pending_currencies(self, currencies, today, same_day_retry):
        """Keep only the currencies that still need a rate for today."""
        return {
            code: currency
            for code, currency in currencies.items()
            if (
                not self._has_exact_rate_for_date(currency, today)
                if code != "UTM"
                else (
                    not same_day_retry and not self._has_rate_coverage(currency, today)
                )
            )
        }

    def _catch_up_pending(self):
        """Fetch the selected source range and create missing rates only."""
        self.ensure_one()
        self._ensure_chilean_company()
        today = self._santiago_today()
        if self.last_sync_date:
            cursor = self.last_sync_date
            start_date = cursor + timedelta(days=1)
        else:
            cursor = None
            start_date = date(today.year, 1, 1)
        if start_date > today:
            if self._has_pending_daily_rates():
                self.last_sync_date = today - timedelta(days=1)
                self.last_sync_status = "partial"
                return True
            self.last_sync_date = today
            self.last_sync_status = "success"
            return True

        currencies = self._get_currency_map()
        same_day_retry = bool(
            self.last_sync_attempt
            and fields.Datetime.to_datetime(self.last_sync_attempt)
            .replace(tzinfo=timezone.utc)
            .astimezone(SANTIAGO_TZ)
            .date()
            == today
        )
        currencies = self._pending_currencies(currencies, today, same_day_retry)
        if not currencies:
            self.last_sync_date = today
            self.last_sync_status = "success"
            return True

        self.last_sync_attempt = fields.Datetime.now()
        all_ok = True
        first_error = False
        for code, currency in currencies.items():
            try:
                records = self._fetch_source_records(code, start_date, today)
                effective_start = start_date
                if code == "UTM":
                    effective_start = date(start_date.year, start_date.month, 1)
                for record in records:
                    if record.get("status", "ok") != "ok":
                        continue
                    rate_date = fields.Date.to_date(record["date"])
                    if rate_date < effective_start or rate_date > today:
                        continue
                    rate_value = self._normalize_source_rate(record.get("value"))
                    if self._create_rate_if_missing(
                        currency,
                        rate_date,
                        rate_value,
                    ):
                        self._log_sync(
                            rate_date,
                            currency,
                            rate_value,
                            "success",
                        )
            except Exception as exc:
                all_ok = False
                first_error = first_error or f"{code}: {exc}"
                self._log_sync(None, currency, None, "failed", str(exc))
                _logger.error(
                    "Bcentral: catch-up failed for %s in %s: %s",
                    code,
                    self.company_id.name,
                    exc,
                )

        daily_pending = self._has_pending_daily_rates()
        if all_ok and daily_pending:
            # Close historical dates, but leave today available for a later publication.
            self.last_sync_date = today - timedelta(days=1)
            self.last_sync_status = "partial"
            self.last_sync_error = "Waiting for today's daily exchange rates."
            self.last_sync_error_date = fields.Datetime.now()
            for code, currency in self._get_currency_map().items():
                if code != "UTM" and not self._has_exact_rate_for_date(currency, today):
                    self._log_sync(
                        today,
                        currency,
                        None,
                        "skipped",
                        f"No exact {code} publication for {today}; retry scheduled.",
                    )
        elif all_ok:
            self.last_sync_date = today
            self.last_sync_status = "success"
            self.last_sync_error = False
            self.last_sync_error_date = False
        else:
            self.last_sync_status = "partial" if cursor else "failed"
            self.last_sync_error = first_error
            self.last_sync_error_date = fields.Datetime.now()
        return all_ok

    def _create_rate_if_missing(self, currency, rate_date, rate_value):
        """Create a source-backed rate without modifying an existing row."""
        rate_date = fields.Date.to_date(rate_date)
        if rate_date > fields.Date.today():
            raise exceptions.ValidationError(
                _("Cannot write a future exchange-rate date: %s") % rate_date
            )
        rate_value = self._normalize_source_rate(rate_value)
        Rate = self.env["res.currency.rate"].sudo()
        existing = Rate.search(
            [
                ("currency_id", "=", currency.id),
                ("name", "=", rate_date),
                ("company_id", "=", self.company_id.root_id.id),
            ],
            limit=1,
        )
        if existing:
            return False
        Rate.create(
            {
                "name": rate_date,
                "inverse_company_rate": rate_value,
                "currency_id": currency.id,
                "company_id": self.company_id.root_id.id,
            }
        )
        return True

    def action_fetch_currency_range(self, currency, date_from, date_to):
        """Fetch and create missing published rates for one currency and range."""
        self.ensure_one()
        if not self.env.user.has_group("account.group_account_manager"):
            raise exceptions.AccessError(
                _("Only accounting managers can retrieve exchange rates.")
            )
        self._ensure_chilean_company()
        date_from = fields.Date.to_date(date_from)
        date_to = fields.Date.to_date(date_to)
        if date_from > date_to:
            raise exceptions.ValidationError(
                _("The start date must be on or before the end date.")
            )
        if date_to > fields.Date.today():
            raise exceptions.ValidationError(_("The end date cannot be in the future."))
        if currency.name not in SUPPORTED_CURRENCY_CODES:
            raise exceptions.ValidationError(
                _("No exchange-rate provider mapping exists for %s.") % currency.name
            )

        fetch_from = date_from
        if currency.name == "UTM":
            fetch_from = date(date_from.year, date_from.month, 1)
        result = {"created": 0, "skipped": 0, "no_publication": 0, "failed": 0}
        try:
            records = self._fetch_source_records(currency.name, fetch_from, date_to)
        except Exception as exc:
            result["failed"] = 1
            safe_error = _("Provider request failed (%s)") % type(exc).__name__
            self._log_sync(None, currency, None, "failed", safe_error)
            result["errors"] = [safe_error]
            return result

        valid_records = []
        errors = []
        for record in records:
            try:
                rate_date = fields.Date.to_date(record["date"])
                if currency.name == "UTM":
                    overlaps = (rate_date.year, rate_date.month) >= (
                        date_from.year,
                        date_from.month,
                    ) and (rate_date.year, rate_date.month) <= (
                        date_to.year,
                        date_to.month,
                    )
                else:
                    overlaps = date_from <= rate_date <= date_to
                if not overlaps:
                    continue
                if record.get("status", "ok") != "ok":
                    result["no_publication"] += 1
                else:
                    valid_records.append((rate_date, record.get("value")))
            except Exception as exc:
                result["failed"] += 1
                safe_error = _("Provider data invalid (%s)") % type(exc).__name__
                errors.append(safe_error)
                self._log_sync(None, currency, None, "failed", safe_error)

        for rate_date, raw_value in valid_records:
            try:
                with self.env.cr.savepoint():
                    rate_value = self._normalize_source_rate(raw_value)
                    created = self._create_rate_if_missing(
                        currency, rate_date, rate_value
                    )
                    if created:
                        result["created"] += 1
                        self._log_sync(rate_date, currency, rate_value, "success")
                    else:
                        result["skipped"] += 1
                        self._log_sync(
                            rate_date,
                            currency,
                            rate_value,
                            "skipped",
                            "Rate already exists; existing value preserved",
                        )
            except Exception as exc:
                result["failed"] += 1
                safe_error = _("Rate on %(date)s failed (%(error)s)") % {
                    "date": rate_date,
                    "error": type(exc).__name__,
                }
                errors.append(safe_error)
                self._log_sync(rate_date, currency, None, "failed", safe_error)

        if not valid_records and not result["no_publication"] and not result["failed"]:
            result["no_publication"] = 1
        result["errors"] = errors
        return result

    def _backfill_current_year(self):
        """Load all historical rates from Jan 1st of current year up to today."""
        self.ensure_one()
        year = self._get_current_year()
        today = fields.Date.today()
        currencies = self._get_currency_map()

        if not currencies:
            _logger.warning(
                "Bcentral: no currencies to backfill for company %s",
                self.company_id.name,
            )
            return

        last_success = None
        all_ok = True
        for code, currency in currencies.items():
            try:
                records = self._fetch_source_records(
                    code, date(year, 1, 1), min(date(year, 12, 31), today)
                )
            except Exception as exc:
                all_ok = False
                _logger.error(
                    "Bcentral: failed to fetch year %s for %s: %s", year, code, exc
                )
                self._log_sync(
                    None,
                    currency,
                    None,
                    "failed",
                    f"Year fetch failed for {code}: {exc}",
                )
                continue

            count = 0
            for rec in records:
                if rec.get("status", "ok") != "ok":
                    continue
                rec_date = fields.Date.to_date(rec["date"])
                if rec_date > today:
                    continue
                rate_value = self._normalize_source_rate(rec.get("value"))
                self._upsert_rate(currency, rec_date, rate_value)
                self._log_sync(rec_date, currency, rate_value, "success")
                count += 1
                if last_success is None or rec_date > last_success:
                    last_success = rec_date

            _logger.info("Bcentral: backfilled %s records for %s", count, code)

        if all_ok and last_success:
            self.last_sync_date = min(last_success, today)
            self.last_sync_status = "success"
            self.last_sync_error = False
            self.last_sync_error_date = False
        elif not all_ok:
            self.last_sync_status = "partial" if self.last_sync_date else "failed"
            self.last_sync_error_date = fields.Datetime.now()

    def ensure_rates_for_dates(self, requirements):
        """Ensure historical coverage for ``[(currency_code, date), ...]``."""
        self.ensure_one()
        self._ensure_chilean_company()
        currency_by_name = {
            currency.name: currency
            for currency in self.env["res.currency"].search(
                [
                    ("name", "in", list({code for code, _date in requirements})),
                ]
            )
        }
        for code, target_date in requirements:
            target_date = fields.Date.to_date(target_date)
            if target_date > fields.Date.today():
                raise exceptions.ValidationError(
                    _("Cannot ensure a future exchange-rate date: %s") % target_date
                )
            if code not in SUPPORTED_CURRENCY_CODES:
                raise exceptions.ValidationError(
                    _("Unsupported exchange-rate currency: %s") % code
                )
            currency = currency_by_name.get(code)
            if not currency:
                raise exceptions.ValidationError(_("Currency %s does not exist") % code)
            if self._has_rate_coverage(currency, target_date):
                continue
            records = self._fetch_source_records(
                code, date(target_date.year, 1, 1), target_date
            )
            eligible = []
            for record in records:
                record_date = fields.Date.to_date(record["date"])
                if record.get("status", "ok") == "ok" and record_date <= target_date:
                    eligible.append(
                        (record_date, self._normalize_source_rate(record.get("value")))
                    )
            for record_date, rate_value in eligible:
                self._upsert_rate(currency, record_date, rate_value)
                self._log_sync(record_date, currency, rate_value, "success")
            if not self._has_rate_coverage(currency, target_date):
                raise exceptions.ValidationError(
                    _("No %(code)s exchange rate available on or before %(date)s")
                    % {"code": code, "date": target_date}
                )

    def _has_rate_coverage(self, currency, target_date):
        """Return the latest company rate applicable to a document date."""
        self.ensure_one()
        return (
            self.env["res.currency.rate"]
            .sudo()
            .search(
                [
                    ("currency_id", "=", currency.id),
                    ("name", "<=", target_date),
                    ("company_id", "=", self.company_id.root_id.id),
                ],
                order="name desc",
                limit=1,
            )
        )

    def action_reconcile_rates(self, repair_future=False):
        """Reconcile configured rates against Bcentral source values.

        ``inverse_company_rate`` is the source value in CLP per unit of the
        foreign currency. Odoo computes ``rate`` as its reciprocal. The
        Future rows are only removed when ``repair_future`` is explicit.
        """
        self.ensure_one()
        self._ensure_chilean_company()
        today = fields.Date.today()
        currencies = self._get_currency_map()
        summary = {
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "skipped": 0,
            "failed": 0,
            "future_found": 0,
            "future_removed": 0,
            "future_ids": [],
        }
        for code, currency in currencies.items():
            try:
                records = self._get_source_records(code, today.year)
                for record in records:
                    if record.get("status", "ok") != "ok":
                        summary["skipped"] += 1
                        continue
                    rate_date = fields.Date.to_date(record["date"])
                    if rate_date > today:
                        summary["skipped"] += 1
                        continue
                    source_value = self._normalize_source_rate(record["value"])
                    result = self._upsert_rate(currency, rate_date, source_value)
                    summary[result] += 1
                    if result != "unchanged":
                        self._log_sync(rate_date, currency, source_value, "success")
            except Exception as exc:
                summary["failed"] += 1
                _logger.error(
                    "Bcentral: reconciliation failed for %s in %s: %s",
                    code,
                    self.company_id.name,
                    exc,
                )
                self._log_sync(None, currency, None, "failed", str(exc))

        Rate = self.env["res.currency.rate"].sudo()
        future_rates = Rate.search(
            [
                (
                    "currency_id",
                    "in",
                    [currency.id for currency in currencies.values()],
                ),
                ("company_id", "=", self.company_id.root_id.id),
                ("name", ">", today),
            ],
            order="name asc, id asc",
        )
        summary["future_found"] = len(future_rates)
        summary["future_ids"] = future_rates.ids
        if repair_future and future_rates:
            future_count = len(future_rates)
            _logger.warning(
                "Bcentral: removing future rates for company %s: ids=%s",
                self.company_id.name,
                future_rates.ids,
            )
            future_rates.unlink()
            summary["future_removed"] = future_count
        return summary

    def _repair_logged_inverted_rates(self):
        """Compatibility entry point using source-grounded reconciliation."""
        _logger.warning(
            "Bcentral: heuristic inverted-rate repair is deprecated; "
            "running source-grounded reconciliation"
        )
        total = 0
        for config in self:
            result = config.action_reconcile_rates(repair_future=True)
            total += result["created"] + result["updated"]
        return total

    def _get_source_records(self, code, year):
        """Return normalized ``date``/``value`` pairs for one year."""
        start_date = date(year, 1, 1)
        end_date = min(date(year, 12, 31), fields.Date.today())
        return self._fetch_source_records(code, start_date, end_date)

    def _fetch_source_records(self, code, start_date, end_date):
        """Fetch one currency range from the Banco Central BDE API."""
        self.ensure_one()
        start_date = fields.Date.to_date(start_date)
        end_date = fields.Date.to_date(end_date)
        token = (
            self.env["ir.config_parameter"].sudo().get_param(BcentralApi.TOKEN_PARAM)
        )
        return BcentralApi.fetch_range(code, start_date, end_date, token)

    @staticmethod
    def _normalize_source_rate(value):
        """Validate a positive Bcentral value before writing it to Odoo."""
        if isinstance(value, bool):
            raise exceptions.ValidationError(_("Exchange rate must be numeric"))
        try:
            normalized = float(value)
        except (TypeError, ValueError):
            raise exceptions.ValidationError(
                _("Exchange rate must be numeric")
            ) from None
        if not math.isfinite(normalized) or normalized <= 0:
            raise exceptions.ValidationError(
                _("Exchange rate must be strictly positive")
            )
        return normalized

    # ---------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------
    def _upsert_rate(self, currency, rate_date, rate_value):
        """Create or update one rate using the source inverse value."""
        rate_date = fields.Date.to_date(rate_date)
        if rate_date > fields.Date.today():
            raise exceptions.ValidationError(
                _("Cannot write a future exchange-rate date: %s") % rate_date
            )
        rate_value = self._normalize_source_rate(rate_value)
        Rate = self.env["res.currency.rate"].sudo()
        existing = Rate.search(
            [
                ("currency_id", "=", currency.id),
                ("name", "=", rate_date),
                ("company_id", "=", self.company_id.root_id.id),
            ],
            limit=1,
        )

        # ``inverse_company_rate`` is a computed field in Odoo 18; writing it on
        # create is discarded and the rate ends up as 1.0. The stored technical
        # rate is what must be written.
        vals = {
            "name": rate_date,
            "rate": 1.0 / rate_value,
            "currency_id": currency.id,
            "company_id": self.company_id.root_id.id,
        }
        if existing:
            if math.isclose(
                existing.inverse_company_rate, rate_value, rel_tol=0, abs_tol=0.000001
            ):
                return "unchanged"
            existing.write(vals)
            return "updated"
        else:
            Rate.create(vals)
            return "created"

    def _log_sync(self, sync_date, currency, rate_value, status, error_message=None):
        """Write a log entry."""
        self.env["l10n.cl.bcentral.log"].sudo().create(
            {
                "config_id": self.id,
                "sync_date": sync_date,
                "currency_id": currency.id if currency else False,
                "status": status,
                "rate_value": rate_value,
                "error_message": error_message,
            }
        )
