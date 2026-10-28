# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import fields, models


class RcvYearWizard(models.TransientModel):
    _name = "l10n.cl.rcv.year.wizard"
    _description = "Select RCV fiscal year"

    year = fields.Integer(required=True, default=lambda self: fields.Date.today().year)

    def action_open_year(self):
        self.ensure_one()
        return self.env["l10n.cl.rcv.period"].action_open_year(self.year)
