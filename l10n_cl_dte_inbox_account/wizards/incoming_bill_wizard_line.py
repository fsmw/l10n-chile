# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class IncomingDteBillWizardLine(models.TransientModel):
    _name = "l10n_cl.incoming.dte.bill.wizard.line"
    _description = "Incoming DTE Vendor Bill Preview Line"
    _check_company_auto = True

    wizard_id = fields.Many2one(
        "l10n_cl.incoming.dte.bill.wizard", required=True, ondelete="cascade"
    )
    company_id = fields.Many2one(
        related="wizard_id.company_id", store=True, readonly=True
    )
    currency_id = fields.Many2one(related="wizard_id.currency_id", readonly=True)
    sequence = fields.Integer(required=True, readonly=True)
    description = fields.Char(required=True, readonly=True)
    source_codes = fields.Json(readonly=True)
    source_quantity = fields.Char(readonly=True)
    source_unit_price = fields.Char(readonly=True)
    source_discount_percent = fields.Char(readonly=True)
    source_unit = fields.Char(readonly=True)
    source_exempt = fields.Boolean(readonly=True)
    source_amount = fields.Monetary(currency_field="currency_id", readonly=True)
    mapping_id = fields.Many2one(
        "l10n_cl.incoming.dte.account.mapping", readonly=True, check_company=True
    )
    product_id = fields.Many2one("product.product", check_company=True)
    account_id = fields.Many2one(
        "account.account",
        check_company=True,
        domain=(
            "[('company_ids', 'in', [company_id]), ('deprecated', '=', "
            "False), ('account_type', 'not in', ['asset_receivable', "
            "'liability_payable', 'off_balance'])]"
        ),
    )
    tax_id = fields.Many2one(
        "account.tax",
        check_company=True,
        domain=("[('company_id', '=', company_id), ('type_tax_use', '=', 'purchase')]"),
    )
    uom_id = fields.Many2one("uom.uom")
    existing_line_id = fields.Many2one(
        "account.move.line",
        check_company=True,
        domain=(
            "[('move_id', '=', parent.existing_move_id), ('display_type', "
            "'=', 'product')]"
        ),
    )
    diagnostic_text = fields.Text(readonly=True)
