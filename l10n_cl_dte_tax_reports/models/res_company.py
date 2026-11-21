# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    l10n_cl_f29_ppm_rate = fields.Float(
        string="PPM Rate (%)",
        digits=(16, 3),
        help="Monthly provisional payment (PPM) rate applied to the net sales "
        "of the period in the F29 worksheet.",
    )
    l10n_cl_f29_proportional_factor = fields.Float(
        string="Proportional Factor (%)",
        digits=(16, 2),
        default=100.0,
        help="Share of the common-use VAT credit that may be used as tax "
        "credit in the F29 worksheet.",
    )
