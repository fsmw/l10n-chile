# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models

# SII codes of the documents a settlement invoice may settle (TpoDocLiq);
# 99 covers advances and other transactions.
SETTLED_DOCUMENT_CODES = [
    ("30", "30 - Invoice"),
    ("32", "32 - Exempt invoice"),
    ("33", "33 - Electronic invoice"),
    ("34", "34 - Exempt electronic invoice"),
    ("35", "35 - Receipt"),
    ("38", "38 - Exempt receipt"),
    ("39", "39 - Electronic receipt"),
    ("41", "41 - Exempt electronic receipt"),
    ("46", "46 - Electronic purchase invoice"),
    ("55", "55 - Debit note"),
    ("56", "56 - Electronic debit note"),
    ("99", "99 - Advance or other transaction"),
]


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    dte_settled_document_code = fields.Selection(
        SETTLED_DOCUMENT_CODES,
        string="Settled Document Type",
        help="On a settlement invoice DTE 43, the SII type of the document that "
        "supported the operation made on behalf of the principal (TpoDocLiq).",
    )
    dte_liquidation_charge = fields.Selection(
        [("C", "Commission"), ("O", "Other charge")],
        string="Agent Charge",
        help="On a settlement invoice DTE 43, marks a negative line as the "
        "agent's commission or other charge (Comisiones block). Leave it empty "
        "on the settled operations.",
    )
