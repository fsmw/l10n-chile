# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class DteDispatch(models.Model):
    _inherit = "l10n_cl.dte.dispatch"

    source_kind = fields.Selection(
        selection_add=[("stock_picking", "Delivery")],
        ondelete={"stock_picking": "cascade"},
    )
    picking_id = fields.Many2one(
        "stock.picking",
        string="Delivery",
        readonly=True,
        copy=False,
        index="btree_not_null",
        ondelete="restrict",
    )
