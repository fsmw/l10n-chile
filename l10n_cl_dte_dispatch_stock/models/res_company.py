# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    dte_dispatch_valued = fields.Boolean(
        string="Valued Dispatch Guides",
        default=True,
        help="Dispatch guides created from deliveries state the price of each "
        "item. Disable to issue unvalued guides.",
    )
