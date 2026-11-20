# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models
from odoo.exceptions import AccessError, UserError

from ..models.rcv_period import DOCUMENT_OPERATIONS, MANAGER_GROUP


class RcvSyncWizard(models.TransientModel):
    _name = "l10n_cl.dte.rcv.sync.wizard"
    _description = "Read RCV from the SII"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        domain=lambda self: [("id", "in", self.env.companies.ids)],
    )
    year = fields.Integer(
        required=True, default=lambda self: fields.Date.context_today(self).year
    )
    month = fields.Integer(
        required=True, default=lambda self: fields.Date.context_today(self).month
    )
    operation = fields.Selection(DOCUMENT_OPERATIONS, required=True)

    def action_sync(self):
        """Queue the SII read of the selected period and open that period."""
        self.ensure_one()
        if not self.env.user.has_group(MANAGER_GROUP):
            raise AccessError(
                self.env._("Only a DTE manager can read the RCV from the SII.")
            )
        if not 1 <= self.month <= 12:
            raise UserError(self.env._("The month must be between 1 and 12."))
        period = self._period()
        period.action_sync()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("RCV Period"),
            "res_model": period._name,
            "res_id": period.id,
            "view_mode": "form",
            "target": "current",
        }

    def _period(self):
        period_model = self.env["l10n_cl.dte.rcv.period"]
        values = {
            "company_id": self.company_id.id,
            "year": self.year,
            "month": self.month,
            "operation": self.operation,
        }
        return period_model.search(
            [(key, "=", value) for key, value in values.items()], limit=1
        ) or period_model.create(values)
