# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.exceptions import AccessError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    dte_inbox_account_enabled = fields.Boolean(
        related="company_id.dte_inbox_account_enabled", readonly=False
    )
    dte_inbox_account_journal_id = fields.Many2one(
        related="company_id.dte_inbox_account_journal_id",
        readonly=False,
        domain=(
            "[('company_id', '=', company_id), ('type', '=', 'purchase'), "
            "('l10n_latam_use_documents', '=', True)]"
        ),
    )
    dte_inbox_account_vat_tax_id = fields.Many2one(
        related="company_id.dte_inbox_account_vat_tax_id",
        readonly=False,
        domain=("[('company_id', '=', company_id), ('type_tax_use', '=', 'purchase')]"),
    )
    dte_inbox_account_exempt_tax_id = fields.Many2one(
        related="company_id.dte_inbox_account_exempt_tax_id",
        readonly=False,
        domain=("[('company_id', '=', company_id), ('type_tax_use', '=', 'purchase')]"),
    )

    @api.model_create_multi
    def create(self, vals_list):
        self.check_access("create")
        sanitized = []
        with self.env.cr.savepoint():
            for vals in vals_list:
                config = {
                    key: value
                    for key, value in vals.items()
                    if key.startswith("dte_inbox_account_")
                }
                if config:
                    company = self.env["res.company"].browse(
                        vals.get("company_id") or self.env.company.id
                    )
                    if company != self.env.company:
                        raise AccessError(
                            self.env._(
                                "Configure incoming DTE bills in the active company."
                            )
                        )
                    company.write(config)
                sanitized.append(
                    {key: value for key, value in vals.items() if key not in config}
                )
            return super().create(sanitized)

    def write(self, vals):
        self.check_access("write")
        config = {
            key: value
            for key, value in vals.items()
            if key.startswith("dte_inbox_account_")
        }
        with self.env.cr.savepoint():
            if config:
                for settings in self:
                    company = self.env["res.company"].browse(
                        vals.get("company_id") or settings.company_id.id
                    )
                    if company != self.env.company:
                        raise AccessError(
                            self.env._(
                                "Configure incoming DTE bills in the active company."
                            )
                        )
                    company.write(config)
            return super().write(
                {key: value for key, value in vals.items() if key not in config}
            )
