# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class RcvYearWizard(models.TransientModel):
    _name = "l10n_cl.dte.rcv.year.wizard"
    _description = "Open RCV Fiscal Year"

    year = fields.Integer(
        required=True, default=lambda self: fields.Date.context_today(self).year
    )

    def action_open_year(self):
        self.ensure_one()
        return self.env["l10n_cl.dte.rcv.period"].action_open_year(self.year)
