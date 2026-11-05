# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.exceptions import AccessError

from ..services.common import MANAGER, decimal, reject


class ResCompany(models.Model):
    _inherit = "res.company"

    dte_inbox_account_enabled = fields.Boolean(
        string="Incoming DTE Vendor Bills", default=False
    )
    dte_inbox_account_journal_id = fields.Many2one(
        "account.journal",
        string="Incoming DTE Purchase Journal",
        check_company=True,
        domain=(
            "[('company_id', '=', id), ('type', '=', 'purchase'), "
            "('l10n_latam_use_documents', '=', True)]"
        ),
    )
    dte_inbox_account_vat_tax_id = fields.Many2one(
        "account.tax",
        string="Incoming DTE Purchase VAT",
        check_company=True,
        domain="[('company_id', '=', id), ('type_tax_use', '=', 'purchase')]",
    )
    dte_inbox_account_exempt_tax_id = fields.Many2one(
        "account.tax",
        string="Incoming DTE Exempt Purchase Tax",
        check_company=True,
        domain="[('company_id', '=', id), ('type_tax_use', '=', 'purchase')]",
    )

    @api.constrains(
        "country_id",
        "account_fiscal_country_id",
        "currency_id",
        "dte_inbox_account_enabled",
        "dte_inbox_account_journal_id",
        "dte_inbox_account_vat_tax_id",
        "dte_inbox_account_exempt_tax_id",
    )
    def _check_dte_inbox_account_configuration(self):
        for company in self.filtered("dte_inbox_account_enabled"):
            journal = company.dte_inbox_account_journal_id
            if (
                company.country_id.code != "CL"
                or company.account_fiscal_country_id.code != "CL"
                or company.currency_id.name != "CLP"
                or decimal(company.currency_id.rounding) != 1
                or not journal
                or not journal.active
                or journal.company_id != company
                or journal.type != "purchase"
                or not journal.l10n_latam_use_documents
            ):
                reject(self.env, "company_disabled")
            for tax, rate, code in (
                (company.dte_inbox_account_vat_tax_id, 19, 14),
                (company.dte_inbox_account_exempt_tax_id, 0, 0),
            ):
                if (
                    not tax
                    or tax.company_id != company
                    or not tax.active
                    or tax.type_tax_use != "purchase"
                    or tax.amount_type != "percent"
                    or decimal(tax.amount) != rate
                    or tax.l10n_cl_sii_code != code
                    or tax.price_include
                    or tax.include_base_amount
                    or tax.children_tax_ids
                ):
                    reject(self.env, "tax_invalid")

    def write(self, vals):
        if (
            any(key.startswith("dte_inbox_account_") for key in vals)
            and not self.env.su
            and not self.env.user.has_group(MANAGER)
        ):
            raise AccessError(
                self.env._("The incoming DTE configuration role is required.")
            )
        return super().write(vals)
