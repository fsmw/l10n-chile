# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class L10nClF29ReportLine(models.TransientModel):
    _name = "l10n_cl.f29.report.line"
    _description = "F29 Monthly VAT Worksheet Line"
    _order = "sequence, id"

    report_id = fields.Many2one("l10n_cl.f29.report", required=True, ondelete="cascade")
    currency_id = fields.Many2one(related="report_id.currency_id")
    sequence = fields.Integer()
    line_type = fields.Selection(
        [("box", "Box"), ("total", "Total")], required=True, default="box"
    )
    section = fields.Selection(
        [
            ("sale", "VAT Debits (Sales)"),
            ("purchase", "VAT Credits (Purchases)"),
            ("withholding", "Withholdings"),
            ("info", "Informative"),
            ("total", "Determination"),
        ],
        required=True,
    )
    box_id = fields.Many2one("l10n_cl.f29.box", ondelete="set null")
    name = fields.Char(required=True)
    count_code = fields.Char(string="Count Box")
    count = fields.Integer(string="Documents")
    amount_code = fields.Char(string="Amount Box")
    amount = fields.Monetary()
