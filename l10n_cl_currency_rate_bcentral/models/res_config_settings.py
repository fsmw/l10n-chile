# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from ..services.bcentral_api import BcentralApi
from .res_company import SUPPORTED_CURRENCY_CODES

BCENTRAL_TOKEN_EXPIRATION_PARAM = (
    "l10n_cl_currency_rate_bcentral.bcentral_token_expiration"
)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    l10n_cl_bcentral_enabled = fields.Boolean(
        string="Enable Bcentral",
        related="company_id.l10n_cl_bcentral_enabled",
        readonly=False,
    )
    l10n_cl_bcentral_currency_ids = fields.Many2many(
        "res.currency",
        related="company_id.l10n_cl_bcentral_currency_ids",
        readonly=False,
        string="Bcentral Currencies",
        domain=[("name", "in", SUPPORTED_CURRENCY_CODES)],
        context={"active_test": False},
    )
    l10n_cl_bcentral_sync_uf = fields.Boolean(
        string="UF",
        related="company_id.l10n_cl_bcentral_sync_uf",
        readonly=False,
    )
    l10n_cl_bcentral_sync_usd = fields.Boolean(
        string="USD",
        related="company_id.l10n_cl_bcentral_sync_usd",
        readonly=False,
    )
    l10n_cl_bcentral_sync_eur = fields.Boolean(
        string="EUR",
        related="company_id.l10n_cl_bcentral_sync_eur",
        readonly=False,
    )
    l10n_cl_bcentral_sync_utm = fields.Boolean(
        string="UTM",
        related="company_id.l10n_cl_bcentral_sync_utm",
        readonly=False,
    )
    l10n_cl_bcentral_interval = fields.Selection(
        related="company_id.l10n_cl_bcentral_interval",
        readonly=False,
    )
    l10n_cl_bcentral_bcentral_token = fields.Char(
        string="Banco Central API Token",
        groups="base.group_system",
        help=(
            "Enter a new token to replace the configured one. The current token is"
            "never displayed."
        ),
    )
    l10n_cl_bcentral_bcentral_token_configured = fields.Boolean(
        compute="_compute_l10n_cl_bcentral_bcentral_token_configured",
        groups="base.group_system",
    )
    l10n_cl_bcentral_bcentral_token_expiration = fields.Date(
        string="Token Expiration Date",
        groups="base.group_system",
        help=(
            "Optional reminder date from the Banco Central account. Renew or extend"
            "the token there, then enter the replacement above."
        ),
    )
    l10n_cl_bcentral_bcentral_token_expiry_warning = fields.Boolean(
        compute="_compute_l10n_cl_bcentral_bcentral_token_expiry_warning",
        groups="base.group_system",
    )
    l10n_cl_bcentral_next_run = fields.Datetime(
        related="company_id.l10n_cl_bcentral_next_run",
        readonly=False,
    )
    l10n_cl_bcentral_is_chilean = fields.Boolean(
        compute="_compute_l10n_cl_bcentral_is_chilean",
    )
    l10n_cl_bcentral_last_sync_date = fields.Date(
        string="Last Successful Sync",
        compute="_compute_l10n_cl_bcentral_status",
    )
    l10n_cl_bcentral_last_sync_attempt = fields.Datetime(
        string="Last Sync Attempt",
        compute="_compute_l10n_cl_bcentral_status",
    )
    l10n_cl_bcentral_sync_pending = fields.Boolean(
        string="Rates Pending",
        compute="_compute_l10n_cl_bcentral_status",
    )
    l10n_cl_bcentral_last_sync_status = fields.Selection(
        [
            ("success", "Success"),
            ("partial", "Partial"),
            ("failed", "Failed"),
        ],
        string="Last Sync Status",
        compute="_compute_l10n_cl_bcentral_status",
    )
    l10n_cl_bcentral_last_sync_error = fields.Text(
        string="Latest Sync Error",
        compute="_compute_l10n_cl_bcentral_status",
    )
    l10n_cl_bcentral_last_sync_error_date = fields.Datetime(
        string="Latest Error Date",
        compute="_compute_l10n_cl_bcentral_status",
    )

    @api.depends("company_id", "company_id.country_id")
    def _compute_l10n_cl_bcentral_is_chilean(self):
        for settings in self:
            settings.l10n_cl_bcentral_is_chilean = (
                settings.company_id.country_id.code == "CL"
            )

    @api.depends()
    def _compute_l10n_cl_bcentral_bcentral_token_configured(self):
        configured = bool(
            self.env["ir.config_parameter"].sudo().get_param(BcentralApi.TOKEN_PARAM)
        )
        for settings in self:
            settings.l10n_cl_bcentral_bcentral_token_configured = configured

    @api.model
    def _get_l10n_cl_bcentral_bcentral_token_expiration(self):
        return (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param(BCENTRAL_TOKEN_EXPIRATION_PARAM)
            or False
        )

    @api.depends("l10n_cl_bcentral_bcentral_token_expiration")
    def _compute_l10n_cl_bcentral_bcentral_token_expiry_warning(self):
        today = fields.Date.today()
        for settings in self:
            expiration = settings.l10n_cl_bcentral_bcentral_token_expiration
            settings.l10n_cl_bcentral_bcentral_token_expiry_warning = bool(
                expiration and expiration <= fields.Date.add(today, days=30)
            )

    @api.model
    def get_values(self):
        values = super().get_values()
        # Never preload a personal token into the browser, even in masked form.
        values["l10n_cl_bcentral_bcentral_token"] = False
        values["l10n_cl_bcentral_bcentral_token_expiration"] = (
            self._get_l10n_cl_bcentral_bcentral_token_expiration()
        )
        return values

    def set_values(self):
        result = super().set_values()
        token = (self.l10n_cl_bcentral_bcentral_token or "").strip()
        if token:
            self.env["ir.config_parameter"].sudo().set_param(
                BcentralApi.TOKEN_PARAM, token
            )
        expiration = self.l10n_cl_bcentral_bcentral_token_expiration
        self.env["ir.config_parameter"].sudo().set_param(
            BCENTRAL_TOKEN_EXPIRATION_PARAM,
            fields.Date.to_string(expiration) if expiration else "",
        )
        return result

    def action_l10n_cl_bcentral_test_bcentral(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(
                _("Only a system administrator can test BDE credentials.")
            )
        token = (self.l10n_cl_bcentral_bcentral_token or "").strip() or self.env[
            "ir.config_parameter"
        ].sudo().get_param(BcentralApi.TOKEN_PARAM)
        try:
            BcentralApi.test_connection(token)
            message = _("Banco Central BDE connection succeeded.")
            notification_type = "success"
        except Exception as exc:
            message = _("Banco Central BDE connection failed: %s") % str(exc)
            notification_type = "warning"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Banco Central BDE"),
                "message": message,
                "type": notification_type,
                "sticky": notification_type != "success",
            },
        }

    @api.depends("company_id")
    def _compute_l10n_cl_bcentral_status(self):
        config_model = (
            self.env["l10n.cl.bcentral.config"].sudo().with_context(active_test=False)
        )
        for settings in self:
            config = config_model.search(
                [("company_id", "=", settings.company_id.id)], limit=1
            )
            settings.l10n_cl_bcentral_last_sync_date = config.last_sync_date
            settings.l10n_cl_bcentral_last_sync_attempt = config.last_sync_attempt
            settings.l10n_cl_bcentral_sync_pending = config.sync_pending
            settings.l10n_cl_bcentral_last_sync_status = config.last_sync_status
            settings.l10n_cl_bcentral_last_sync_error = config.last_sync_error
            settings.l10n_cl_bcentral_last_sync_error_date = config.last_sync_error_date

    @api.constrains(
        "l10n_cl_bcentral_enabled",
        "l10n_cl_bcentral_currency_ids",
        "l10n_cl_bcentral_interval",
        "l10n_cl_bcentral_next_run",
    )
    def _check_l10n_cl_bcentral_company(self):
        for settings in self:
            if (
                settings.l10n_cl_bcentral_enabled
                and settings.company_id.country_id.code != "CL"
            ):
                raise ValidationError(
                    _("Bcentral solo puede habilitarse para compañías chilenas.")
                )

    def _get_l10n_cl_bcentral_config(self):
        self.ensure_one()
        return self.company_id._get_l10n_cl_bcentral_config()

    def action_l10n_cl_bcentral_sync(self):
        self.ensure_one()
        if not self.l10n_cl_bcentral_is_chilean:
            raise ValidationError(
                _("Bcentral solo está disponible para compañías chilenas.")
            )
        return self._get_l10n_cl_bcentral_config().action_sync_manual()

    def action_l10n_cl_bcentral_backfill(self):
        self.ensure_one()
        if not self.l10n_cl_bcentral_is_chilean:
            raise ValidationError(
                _("Bcentral solo está disponible para compañías chilenas.")
            )
        return self._get_l10n_cl_bcentral_config().action_backfill_current_year()
