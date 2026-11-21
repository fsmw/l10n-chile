# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    l10n_cl_f29_ppm_rate = fields.Float(
        related="company_id.l10n_cl_f29_ppm_rate", readonly=False
    )
    l10n_cl_f29_proportional_factor = fields.Float(
        related="company_id.l10n_cl_f29_proportional_factor", readonly=False
    )
