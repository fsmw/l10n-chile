# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models

COMPONENTS = [
    ("vat_amount", "Recoverable VAT"),
    ("vat_not_recoverable", "Non-Recoverable VAT"),
    ("common_vat", "Common Use VAT"),
    ("exempt", "Exempt"),
    ("net", "Unclassified Net Amount"),
]


class RcvDraftWizardLine(models.TransientModel):
    _name = "l10n_cl.dte.rcv.draft.wizard.line"
    _description = "RCV Vendor Bill Draft Line"
    _order = "sequence, id"
    _check_company_auto = True

    wizard_id = fields.Many2one(
        "l10n_cl.dte.rcv.draft.wizard", required=True, ondelete="cascade"
    )
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(related="wizard_id.company_id")
    currency_id = fields.Many2one(related="wizard_id.currency_id")
    name = fields.Char(string="Description", required=True)
    price_unit = fields.Monetary(
        string="Amount", required=True, currency_field="currency_id"
    )
    account_id = fields.Many2one(
        "account.account",
        check_company=True,
        domain="[('company_ids', 'in', [company_id]), ('deprecated', '=', False), "
        "('account_type', 'not in', ['asset_receivable', 'liability_payable', "
        "'off_balance'])]",
    )
    tax_ids = fields.Many2many(
        "account.tax",
        string="Taxes",
        check_company=True,
        domain="[('company_id', '=', company_id), ('type_tax_use', '=', 'purchase')]",
    )
    component = fields.Selection(COMPONENTS, string="RCV Component", readonly=True)
