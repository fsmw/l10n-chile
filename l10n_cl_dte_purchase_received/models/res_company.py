# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

from odoo.addons.l10n_cl_dte_inbox_account.services.common import decimal

from ..services.common import (
    PARTIAL_RETENTION_RATES,
    TOTAL_RETENTION_CODE,
    TOTAL_RETENTION_RATE,
    VAT_RATE,
    VAT_SII_CODE,
    plain_tax,
    reject_received,
)

UNSUITABLE_TAX_ACCOUNT_TYPES = frozenset(
    {"asset_receivable", "liability_payable", "off_balance", "income", "expense"}
)


class ResCompany(models.Model):
    _inherit = "res.company"

    # The dte_inbox_account_ prefix keeps the shared configuration-role guard.
    dte_inbox_account_received46_journal_id = fields.Many2one(
        "account.journal",
        string="Received DTE 46 Sale Journal",
        check_company=True,
        domain=(
            "[('company_id', '=', id), ('type', '=', 'sale'), "
            "('l10n_latam_use_documents', '=', True)]"
        ),
        help="Dedicated sale journal for purchase invoices our buyers issue to us.",
    )
    dte_inbox_account_received46_vat_tax_id = fields.Many2one(
        "account.tax",
        string="Received DTE 46 Sales VAT",
        check_company=True,
        domain="[('company_id', '=', id), ('type_tax_use', '=', 'sale')]",
    )
    dte_inbox_account_received46_retention_tax_id = fields.Many2one(
        "account.tax",
        string="VAT Retained by the Buyer",
        check_company=True,
        domain="[('company_id', '=', id), ('type_tax_use', '=', 'sale')]",
        help=(
            "Sale tax at -19% with SII code 15 whose tax account holds the VAT"
            " the buyer retained on our sale."
        ),
    )

    dte_inbox_account_received46_partial_tax_ids = fields.Many2many(
        "account.tax",
        "res_company_received46_partial_tax_rel",
        "company_id",
        "tax_id",
        string="Partial VAT Retained by the Buyer",
        check_company=True,
        domain="[('company_id', '=', id), ('type_tax_use', '=', 'sale')]",
        help=(
            "One sale tax per SII partial retention code (30-48) the buyers"
            " use, at the negative official rate of that code (for example"
            " -8% with SII code 32 for cattle), whose tax account holds the"
            " VAT retained on our sale."
        ),
    )

    @api.constrains(
        "dte_inbox_account_received46_journal_id",
        "dte_inbox_account_received46_vat_tax_id",
        "dte_inbox_account_received46_retention_tax_id",
        "dte_inbox_account_received46_partial_tax_ids",
    )
    def _check_dte_received46_settings(self):
        for company in self:
            if company.dte_inbox_account_received46_journal_id:
                company._check_dte_received46_journal(
                    company.dte_inbox_account_received46_journal_id
                )
            for tax, rate, code in company._dte_received46_tax_specs():
                if tax:
                    company._check_dte_received46_tax(tax, rate, code)
            company._check_dte_received46_partial_taxes()

    def _check_dte_received46_partial_taxes(self):
        """Each mapped tax uses a supported code at its official rate, once."""
        self.ensure_one()
        taxes = self.dte_inbox_account_received46_partial_tax_ids
        codes = [str(tax.l10n_cl_sii_code) for tax in taxes]
        if len(set(codes)) != len(codes):
            reject_received(self.env, "received46_not_configured")
        for tax, code in zip(taxes, codes, strict=True):
            if code not in PARTIAL_RETENTION_RATES:
                reject_received(
                    self.env, "received46_not_configured", source_value=code
                )
            self._check_dte_received46_tax(
                tax, -PARTIAL_RETENTION_RATES[code], int(code)
            )

    def _dte_received46_partial_taxes(self):
        """Return {SII code: sale tax} of the configured partial retentions."""
        self.ensure_one()
        return {
            str(tax.l10n_cl_sii_code): tax
            for tax in self.dte_inbox_account_received46_partial_tax_ids
        }

    def _dte_received46_tax_specs(self):
        self.ensure_one()
        return (
            (self.dte_inbox_account_received46_vat_tax_id, VAT_RATE, VAT_SII_CODE),
            (
                self.dte_inbox_account_received46_retention_tax_id,
                -TOTAL_RETENTION_RATE,
                int(TOTAL_RETENTION_CODE),
            ),
        )

    def _check_dte_received46_journal(self, journal):
        self.ensure_one()
        if (
            not journal
            or not journal.active
            or journal.company_id != self
            or journal.type != "sale"
            or not journal.l10n_latam_use_documents
            or not journal.default_account_id
        ):
            reject_received(self.env, "received46_not_configured")

    def _check_dte_received46_tax(self, tax, rate, code):
        self.ensure_one()
        if not plain_tax(tax, self, "sale", rate, code):
            reject_received(self.env, "received46_not_configured")
        # Without a tax account Odoo would book the tax on the revenue line.
        repartition = tax.invoice_repartition_line_ids.filtered(
            lambda line: line.repartition_type == "tax"
        )
        if (
            not repartition
            or any(
                not line.account_id
                or line.account_id.account_type in UNSUITABLE_TAX_ACCOUNT_TYPES
                for line in repartition
            )
            or decimal(sum(repartition.mapped("factor_percent"))) != 100
        ):
            reject_received(self.env, "received46_not_configured")

    def _get_dte_received46_configuration(self):
        """Return the journal and taxes that book a received DTE 46, or refuse.

        Partial retention taxes are optional; a document using an unmapped
        code is refused when it is prepared.
        """
        self.ensure_one()
        if (
            self.country_id.code != "CL"
            or self.account_fiscal_country_id.code != "CL"
            or self.currency_id.name != "CLP"
            or decimal(self.currency_id.rounding) != 1
        ):
            reject_received(self.env, "received46_not_configured")
        self._check_dte_received46_journal(self.dte_inbox_account_received46_journal_id)
        for tax, rate, code in self._dte_received46_tax_specs():
            if not tax:
                reject_received(self.env, "received46_not_configured")
            self._check_dte_received46_tax(tax, rate, code)
        self._check_dte_received46_partial_taxes()
        return dict(
            journal=self.dte_inbox_account_received46_journal_id,
            vat_tax=self.dte_inbox_account_received46_vat_tax_id,
            retention_tax=self.dte_inbox_account_received46_retention_tax_id,
            partial_retention_taxes=self.dte_inbox_account_received46_partial_tax_ids,
        )
