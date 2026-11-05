# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    dte_pos_returns_enabled = fields.Boolean(
        related="company_id.dte_pos_returns_enabled",
        readonly=False,
        groups="l10n_cl_dte_core.group_dte_manager",
    )
