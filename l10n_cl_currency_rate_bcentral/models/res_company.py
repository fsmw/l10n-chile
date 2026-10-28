# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

SUPPORTED_CURRENCY_CODES = ("UF", "USD", "EUR", "UTM")

BCENTRAL_COMPANY_FIELDS = {
    "active": "l10n_cl_bcentral_enabled",
    "sync_uf": "l10n_cl_bcentral_sync_uf",
    "sync_usd": "l10n_cl_bcentral_sync_usd",
    "sync_eur": "l10n_cl_bcentral_sync_eur",
    "sync_utm": "l10n_cl_bcentral_sync_utm",
}
LEGACY_CURRENCY_FIELDS = {
    "sync_uf": "UF",
    "sync_usd": "USD",
    "sync_eur": "EUR",
    "sync_utm": "UTM",
}


class ResCompany(models.Model):
    _inherit = "res.company"

    l10n_cl_bcentral_enabled = fields.Boolean(
        string="Enable Bcentral",
        default=False,
        help="Synchronize Chilean exchange rates from Bcentral for this company.",
    )
    l10n_cl_bcentral_currency_ids = fields.Many2many(
        "res.currency",
        "res_company_l10n_cl_bcentral_currency_rel",
        "company_id",
        "currency_id",
        string="Bcentral Currencies",
        domain=[("name", "in", SUPPORTED_CURRENCY_CODES)],
        context={"active_test": False},
        default=lambda self: self._default_l10n_cl_bcentral_currencies(),
        help=(
            "Currencies selected for Bcentral synchronization. Tag order is visual"
            "only."
        ),
    )
    # These fields are retained as compatibility adapters for integrations and
    # older views. The Many2many above is the authoritative selection.
    l10n_cl_bcentral_sync_uf = fields.Boolean(string="UF", default=True)
    l10n_cl_bcentral_sync_usd = fields.Boolean(string="USD", default=True)
    l10n_cl_bcentral_sync_eur = fields.Boolean(string="EUR", default=True)
    l10n_cl_bcentral_sync_utm = fields.Boolean(string="UTM", default=True)
    l10n_cl_bcentral_interval = fields.Selection(
        [
            ("daily", "Daily"),
            ("weekly", "Weekly"),
            ("monthly", "Monthly"),
            ("manual", "Manual only"),
        ],
        string="Synchronization Interval",
        default="daily",
        required=True,
    )
    l10n_cl_bcentral_next_run = fields.Datetime(
        string="Next Synchronization",
        default=fields.Datetime.now,
    )

    @api.model
    def _default_l10n_cl_bcentral_currencies(self):
        currencies = (
            self.env["res.currency"]
            .with_context(active_test=False)
            .search([("name", "in", SUPPORTED_CURRENCY_CODES)])
        )
        return currencies.ids

    @api.constrains("l10n_cl_bcentral_currency_ids")
    def _check_l10n_cl_bcentral_currencies(self):
        for company in self:
            unsupported = set(
                company.l10n_cl_bcentral_currency_ids.mapped("name")
            ) - set(SUPPORTED_CURRENCY_CODES)
            if unsupported:
                raise ValidationError(
                    _("Unsupported Bcentral currencies: %s")
                    % ", ".join(sorted(unsupported))
                )

    @api.constrains("country_id", "l10n_cl_bcentral_enabled")
    def _check_l10n_cl_bcentral_country(self):
        for company in self:
            if company.l10n_cl_bcentral_enabled and company.country_id.code != "CL":
                raise ValidationError(
                    _("Bcentral solo puede habilitarse para compañías chilenas.")
                )

    def _legacy_currency_values(self):
        self.ensure_one()
        selected = set(self.l10n_cl_bcentral_currency_ids.mapped("name"))
        return {
            f"l10n_cl_bcentral_{field_name}": code in selected
            for field_name, code in LEGACY_CURRENCY_FIELDS.items()
        }

    def _currency_ids_for_legacy_values(self, values):
        self.ensure_one()
        selected = set(self.l10n_cl_bcentral_currency_ids.ids)
        currencies = (
            self.env["res.currency"]
            .with_context(active_test=False)
            .search([("name", "in", SUPPORTED_CURRENCY_CODES)])
        )
        by_code = {currency.name: currency.id for currency in currencies}
        for field_name, code in LEGACY_CURRENCY_FIELDS.items():
            if field_name in values and code in by_code:
                if values[field_name]:
                    selected.add(by_code[code])
                else:
                    selected.discard(by_code[code])
        return list(selected)

    def _sync_legacy_currency_fields(self):
        for company in self:
            values = company._legacy_currency_values()
            super(
                ResCompany, company.with_context(skip_bcentral_legacy_sync=True)
            ).write(values)
        return self

    def _get_l10n_cl_bcentral_config(self, create=True):
        self.ensure_one()
        config_model = (
            self.env["l10n.cl.bcentral.config"].sudo().with_context(active_test=False)
        )
        config = config_model.search([("company_id", "=", self.id)], limit=1)
        if not config and create:
            config = config_model.create(
                {
                    "company_id": self.id,
                    **{
                        legacy: self[company_field]
                        for legacy, company_field in BCENTRAL_COMPANY_FIELDS.items()
                    },
                }
            )
        return config

    def _sync_l10n_cl_bcentral_config(self):
        config_model = (
            self.env["l10n.cl.bcentral.config"].sudo().with_context(active_test=False)
        )
        configs = config_model.search([("company_id", "in", self.ids)])
        for config in configs:
            values = {
                legacy: config.company_id[company_field]
                for legacy, company_field in BCENTRAL_COMPANY_FIELDS.items()
            }
            config.with_context(skip_bcentral_company_sync=True).write(values)

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for vals in vals_list:
            vals = dict(vals)
            legacy_values = {
                field_name: vals[f"l10n_cl_bcentral_{field_name}"]
                for field_name in LEGACY_CURRENCY_FIELDS
                if f"l10n_cl_bcentral_{field_name}" in vals
            }
            if legacy_values and "l10n_cl_bcentral_currency_ids" not in vals:
                # Defaults are not available until the parent record exists, so
                # let the post-create adapter project explicit legacy values.
                vals["l10n_cl_bcentral_currency_ids"] = [
                    (6, 0, self._default_l10n_cl_bcentral_currencies())
                ]
            prepared.append(vals)
        companies = super().create(prepared)
        for company, vals in zip(companies, prepared, strict=False):
            legacy_values = {
                field_name: vals[f"l10n_cl_bcentral_{field_name}"]
                for field_name in LEGACY_CURRENCY_FIELDS
                if f"l10n_cl_bcentral_{field_name}" in vals
            }
            if legacy_values:
                company.write(
                    {
                        "l10n_cl_bcentral_currency_ids": [
                            (
                                6,
                                0,
                                company._currency_ids_for_legacy_values(legacy_values),
                            )
                        ]
                    }
                )
            company._sync_legacy_currency_fields()
            if "l10n.cl.bcentral.config" in self.env:
                company._get_l10n_cl_bcentral_config()
        return companies

    def write(self, vals):
        vals = dict(vals)
        legacy_values = {
            field_name: vals[f"l10n_cl_bcentral_{field_name}"]
            for field_name in LEGACY_CURRENCY_FIELDS
            if f"l10n_cl_bcentral_{field_name}" in vals
        }
        if legacy_values and "l10n_cl_bcentral_currency_ids" not in vals:
            if len(self) == 1:
                vals["l10n_cl_bcentral_currency_ids"] = [
                    (6, 0, self._currency_ids_for_legacy_values(legacy_values))
                ]
            else:
                # A single relation command cannot represent different current
                # selections for a multi-company legacy write.
                for company in self:
                    company.write(vals)
                return True
        result = super().write(vals)
        if "l10n_cl_bcentral_currency_ids" in vals:
            self._sync_legacy_currency_fields()
        changed_fields = {
            legacy: company_field
            for legacy, company_field in BCENTRAL_COMPANY_FIELDS.items()
            if company_field in vals
        }
        if changed_fields and not self.env.context.get("skip_bcentral_company_sync"):
            self._sync_l10n_cl_bcentral_config()
        return result
