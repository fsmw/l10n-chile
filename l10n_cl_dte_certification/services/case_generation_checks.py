# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Company configuration checks shared by the case generation service."""

from odoo import _
from odoo.exceptions import ValidationError


class CaseGenerationChecksMixin:
    """Validate the accounting configuration a generated case depends on."""

    def _check_journal(self, journal, company):
        if (
            not journal
            or journal.company_id != company
            or not company.country_id
            or company.country_id.code != "CL"
            or company.currency_id.name != "CLP"
            or journal.type != "sale"
            or not journal.active
            or not journal.l10n_latam_use_documents
        ):
            raise ValidationError(
                _(
                    "Choose an active sales journal enabled for Chilean "
                    "electronic documents."
                )
            )

    def _check_vat(self, tax, company):
        if (
            not tax
            or tax.company_id != company
            or tax.type_tax_use != "sale"
            or tax.amount != 19
            or tax.amount_type != "percent"
            or tax.children_tax_ids
        ):
            raise ValidationError(
                _("Choose a single 19% sales VAT tax for type-33 cases.")
            )

    def _check_income_account(self, account, company):
        if (
            not account
            or company not in account.company_ids
            or account.account_type != "income"
            or account.deprecated
        ):
            raise ValidationError(
                _("Choose an active sales income account for generated lines.")
            )

    def _check_partner(self, partner, company, line):
        if not partner:
            raise ValidationError(
                _("Choose a customer for case %(case)s.", case=line.case_identifier)
            )
        if partner.company_id and partner.company_id != company:
            raise ValidationError(_("The customer belongs to another company."))
        if (
            not partner.active
            or not partner.vat
            or not partner.country_id
            or partner.country_id.code != "CL"
        ):
            raise ValidationError(
                _(
                    "Choose an active Chilean customer with a RUT for case %(case)s.",
                    case=line.case_identifier,
                )
            )
        caf = self.env["l10n_cl.caf.authorization"]
        normalized_vat = caf._normalize_vat(partner.vat)
        if not caf._is_valid_vat(normalized_vat):
            raise ValidationError(
                _(
                    "The customer RUT is invalid for case %(case)s.",
                    case=line.case_identifier,
                )
            )
