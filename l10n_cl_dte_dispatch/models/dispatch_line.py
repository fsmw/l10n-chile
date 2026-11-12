# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

DISPATCH_MODEL = "l10n_cl.dte.dispatch"


class DteDispatchLine(models.Model):
    _name = "l10n_cl.dte.dispatch.line"
    _description = "Electronic dispatch guide line"
    _order = "dispatch_id, sequence, id"

    dispatch_id = fields.Many2one(
        "l10n_cl.dte.dispatch",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one("product.product")
    description = fields.Char(required=True)
    quantity = fields.Float(required=True, digits="Product Unit of Measure")
    uom_id = fields.Many2one("uom.uom", required=True)
    currency_id = fields.Many2one(
        related="dispatch_id.currency_id",
        store=True,
    )
    unit_price = fields.Monetary(currency_field="currency_id")
    tax_category = fields.Selection(
        [("taxable", "Taxable"), ("exempt", "Exempt"), ("unvalued", "Unvalued")],
        required=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        dispatch_ids = [vals["dispatch_id"] for vals in vals_list]
        self.env[DISPATCH_MODEL].browse(dispatch_ids)._check_not_frozen()
        return super().create(vals_list)

    def write(self, vals):
        self.dispatch_id._check_not_frozen()
        if "dispatch_id" in vals:
            self.env[DISPATCH_MODEL].browse(vals["dispatch_id"])._check_not_frozen()
        return super().write(vals)

    def unlink(self):
        self.dispatch_id._check_not_frozen()
        return super().unlink()
