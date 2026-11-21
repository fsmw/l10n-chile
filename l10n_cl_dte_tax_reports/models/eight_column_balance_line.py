# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class L10nClEightColumnBalanceLine(models.TransientModel):
    _name = "l10n_cl.eight.column.balance.line"
    _description = "Eight-Column Balance Line"
    _order = "sequence, id"

    report_id = fields.Many2one(
        "l10n_cl.eight.column.balance", required=True, ondelete="cascade"
    )
    company_id = fields.Many2one(related="report_id.company_id")
    currency_id = fields.Many2one(related="report_id.currency_id")
    sequence = fields.Integer()
    line_type = fields.Selection(
        [
            ("account", "Account"),
            ("unallocated", "Unallocated Earnings"),
            ("result", "Result of the Period"),
        ],
        required=True,
        default="account",
    )
    account_id = fields.Many2one("account.account", ondelete="set null")
    account_type = fields.Selection(related="account_id.account_type")
    name = fields.Char(required=True)
    debit = fields.Monetary()
    credit = fields.Monetary()
    debit_balance = fields.Monetary()
    credit_balance = fields.Monetary()
    assets = fields.Monetary()
    liabilities = fields.Monetary()
    losses = fields.Monetary()
    gains = fields.Monetary()
