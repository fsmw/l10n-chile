# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import fields, models


class BcentralLog(models.Model):
    _name = "l10n.cl.bcentral.log"
    _description = "Bcentral Sync Log"
    _order = "create_date desc"
    _rec_name = "sync_date"

    config_id = fields.Many2one(
        "l10n.cl.bcentral.config",
        string="Configuration",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sync_date = fields.Date(index=True)
    currency_id = fields.Many2one(
        "res.currency",
        index=True,
    )
    status = fields.Selection(
        [
            ("success", "Success"),
            ("failed", "Failed"),
            ("skipped", "Skipped"),
        ],
        required=True,
        index=True,
    )
    rate_value = fields.Float(digits=(16, 6))
    error_message = fields.Text()
    company_id = fields.Many2one(
        "res.company",
        related="config_id.company_id",
        store=True,
        readonly=True,
    )
