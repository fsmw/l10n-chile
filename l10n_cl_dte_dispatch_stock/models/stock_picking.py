# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import UserError

DISPATCH_MODEL = "l10n_cl.dte.dispatch"
# SII IndTraslado 1: the transfer is a sale; TipoDespacho 2: the issuer
# delivers to the customer's premises. Both stay editable on the draft guide.
SALE_TRANSFER = "1"
ISSUER_DELIVERY = "2"


def _address(partner):
    return " ".join(
        part.strip() for part in (partner.street, partner.street2) if part
    ) or (partner.city or "")


class StockPicking(models.Model):
    _inherit = "stock.picking"

    dte_dispatch_ids = fields.One2many(
        DISPATCH_MODEL, "picking_id", string="Dispatch Guides", readonly=True
    )

    def action_create_dte_dispatch(self):
        """Create the draft DTE 52 guide of a validated delivery."""
        self.ensure_one()
        if self.state != "done" or self.picking_type_code != "outgoing":
            raise UserError(_("Only a validated delivery can have a dispatch guide."))
        if self.dte_dispatch_ids:
            raise UserError(_("This delivery already has a dispatch guide."))
        partner = self.partner_id
        if not partner:
            raise UserError(_("Set the customer of the delivery first."))
        company = self.company_id
        dispatch = self.env[DISPATCH_MODEL].create(
            {
                "company_id": company.id,
                "sii_environment": (
                    "production"
                    if company.dte_sii_environment == "production"
                    else "certification"
                ),
                "source_kind": "stock_picking",
                "source_key": f"stock.picking:{self.id}",
                "picking_id": self.id,
                "issue_date": fields.Date.context_today(self),
                "partner_id": partner.id,
                "transfer_indicator": SALE_TRANSFER,
                "dispatch_type_code": ISSUER_DELIVERY,
                "origin_address": _address(
                    self.picking_type_id.warehouse_id.partner_id or company.partner_id
                ),
                "destination_address": _address(partner),
                "destination_commune": partner.city or False,
                "currency_id": company.currency_id.id,
                "line_ids": [
                    fields.Command.create(self._dte_dispatch_line(move))
                    for move in self.move_ids.filtered(lambda m: m.quantity > 0)
                ],
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": DISPATCH_MODEL,
            "res_id": dispatch.id,
            "view_mode": "form",
            "target": "current",
        }

    def _dte_dispatch_line(self, move):
        values = {
            "product_id": move.product_id.id,
            "description": (move.product_id.display_name or move.name or "")[:80],
            "quantity": move.quantity,
            "uom_id": move.product_uom.id,
        }
        if not self.company_id.dte_dispatch_valued:
            return dict(values, unit_price=0.0, tax_category="unvalued")
        sale_line = getattr(move, "sale_line_id", False)
        taxes = sale_line.tax_id if sale_line else move.product_id.taxes_id
        taxes = taxes.filtered(lambda tax: tax.company_id == self.company_id)
        return dict(
            values,
            unit_price=sale_line.price_unit if sale_line else move.product_id.lst_price,
            tax_category="taxable" if taxes.filtered("amount") else "exempt",
        )

    def action_view_dte_dispatch(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": DISPATCH_MODEL,
            "res_id": self.dte_dispatch_ids[:1].id,
            "view_mode": "form",
            "target": "current",
        }
