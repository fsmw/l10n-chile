# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    dte_sii_environment = fields.Selection(
        [
            ("certification", "Certification"),
            ("production", "Production"),
        ],
        string="DTE SII Environment",
        required=True,
        default="certification",
        groups="l10n_cl_dte_core.group_dte_manager",
    )
