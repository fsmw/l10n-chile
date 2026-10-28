# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import fields, models


class RcvSyncWizard(models.TransientModel):
    _name = "l10n.cl.rcv.sync.wizard"
    _description = "Synchronize SII RCV"

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    year = fields.Integer(required=True, default=lambda self: fields.Date.today().year)
    month = fields.Integer(
        required=True, default=lambda self: fields.Date.today().month
    )
    operation = fields.Selection(
        [("purchase", "Compras"), ("sale", "Ventas"), ("boleta", "Ventas Boletas")],
        required=True,
    )

    def action_sync(self):
        self.ensure_one()
        period = self.env["l10n.cl.rcv.period"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("year", "=", self.year),
                ("month", "=", self.month),
                ("operation", "=", self.operation),
            ],
            limit=1,
        )
        if not period:
            period = self.env["l10n.cl.rcv.period"].create(
                {
                    "company_id": self.company_id.id,
                    "year": self.year,
                    "month": self.month,
                    "operation": self.operation,
                }
            )
        period.action_sync()
        return {
            "type": "ir.actions.act_window",
            "res_model": "l10n.cl.rcv.period",
            "res_id": period.id,
            "view_mode": "form",
            "target": "current",
        }
