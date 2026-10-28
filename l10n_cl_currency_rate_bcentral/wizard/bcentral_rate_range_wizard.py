# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class BcentralRateRangeWizard(models.TransientModel):
    _name = "l10n.cl.bcentral.rate.range.wizard"
    _description = "Retrieve Bcentral Rates for a Date Range"

    currency_id = fields.Many2one(
        "res.currency",
        required=True,
        readonly=True,
        default=lambda self: self._default_currency(),
    )
    date_from = fields.Date(required=True, default=fields.Date.today)
    date_to = fields.Date(required=True, default=fields.Date.today)

    @api.model
    def _default_currency(self):
        currency_id = self.env.context.get("active_id")
        return (
            currency_id
            if self.env.context.get("active_model") == "res.currency"
            else False
        )

    def action_fetch_rates(self):
        self.ensure_one()
        if not self.env.user.has_group("account.group_account_manager"):
            raise AccessError(
                _("Only accounting managers can retrieve exchange rates.")
            )
        company = self.env.company
        config = (
            self.env["l10n.cl.bcentral.config"]
            .sudo()
            .search([("company_id", "=", company.id)], limit=1)
        )
        if not config:
            raise UserError(_("Bcentral is not configured for the active company."))
        result = config.with_user(self.env.user).action_fetch_currency_range(
            self.currency_id, self.date_from, self.date_to
        )
        message = _(
            "Created: %(created)s; skipped existing: %(skipped)s; "
            "no publication: %(empty)s; failed: %(failed)s."
        ) % {
            "created": result["created"],
            "skipped": result["skipped"],
            "empty": result["no_publication"],
            "failed": result["failed"],
        }
        if result["errors"]:
            message = f"{message} {' '.join(result['errors'])}"
        has_failures = bool(result["failed"])
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Obtener Tasas"),
                "message": message,
                "type": "warning" if has_failures else "success",
                "sticky": has_failures,
            },
        }
