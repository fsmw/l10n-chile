# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ResCompany(models.Model):
    _inherit = "res.company"

    dte_certification_resolution_date = fields.Date(
        string="Certification Resolution Date",
        help=(
            "Use the date published for this company in the SII Certification "
            "registry. It is independent of the Production resolution date."
        ),
    )

    dte_certification_journal_id = fields.Many2one(
        "account.journal",
        string="Certification Sales Journal",
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    dte_certification_vat_tax_id = fields.Many2one(
        "account.tax",
        string="Certification VAT Tax",
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    dte_certification_exempt_tax_id = fields.Many2one(
        "account.tax",
        string="Certification Exempt Sales Tax",
        groups="l10n_cl_dte_core.group_dte_manager",
        help="Optional; exempt invoice lines can be created without a tax.",
    )

    # Compatibility alias for integrations; the SII environment is authoritative.
    dte_certification_enabled = fields.Boolean(
        string="SII Certification Available",
        compute="_compute_dte_certification_enabled",
        groups="base.group_system,l10n_cl_dte_core.group_dte_manager",
        help="Automatically available when the SII environment is Certification.",
    )

    @api.depends("dte_sii_environment")
    def _compute_dte_certification_enabled(self):
        for company in self:
            company.dte_certification_enabled = (
                company.dte_sii_environment == "certification"
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._check_certification_environment_values(vals)
        companies = super().create(vals_list)
        companies.sudo().filtered(
            lambda company: company.dte_sii_environment == "certification"
        )._ensure_certification_accounting_defaults()
        return companies

    def write(self, vals):
        self._check_certification_environment_values(vals)
        result = super().write(vals)
        if vals.get("dte_sii_environment") == "certification":
            self._ensure_certification_accounting_defaults()
        return result

    @api.model
    def _check_certification_environment_values(self, vals):
        if "dte_certification_enabled" in vals:
            raise UserError(
                _("Certification availability is determined by the SII environment.")
            )

    def _ensure_certification_accounting_defaults(self):
        """Prepare shared Certification accounting defaults without fake taxes."""
        companies = self.filtered(lambda company: company.country_id.code == "CL")
        clp = self.env["res.currency"].search([("name", "=", "CLP")], limit=1)
        journal_model = self.env["account.journal"].sudo()
        tax_model = self.env["account.tax"].sudo()
        for company in companies:
            values = {}
            if not company.dte_certification_journal_id and clp:
                journal = journal_model.search(
                    [
                        ("company_id", "=", company.id),
                        ("type", "=", "sale"),
                        ("name", "=", _("DTE Certification")),
                    ],
                    limit=1,
                )
                if not journal:
                    used_codes = set(
                        journal_model.search([("company_id", "=", company.id)]).mapped(
                            "code"
                        )
                    )
                    code = next(
                        (
                            candidate
                            for candidate in ("DTEC",)
                            + tuple(
                                f"DTC{digit}"
                                for digit in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                            )
                            if candidate not in used_codes
                        ),
                        False,
                    )
                    if not code:
                        raise UserError(
                            _(
                                "No journal code is available for the DTE "
                                "Certification journal."
                            )
                        )
                    journal = journal_model.create(
                        {
                            "name": _("DTE Certification"),
                            "code": code,
                            "type": "sale",
                            "company_id": company.id,
                            "currency_id": clp.id,
                            "l10n_latam_use_documents": True,
                        }
                    )
                values["dte_certification_journal_id"] = journal.id

            if not company.dte_certification_vat_tax_id:
                tax = tax_model.search(
                    [
                        ("company_id", "=", company.id),
                        ("type_tax_use", "=", "sale"),
                        ("amount_type", "=", "percent"),
                        ("amount", "=", 19),
                        ("l10n_cl_sii_code", "=", 14),
                    ],
                    limit=1,
                )
                if tax:
                    values["dte_certification_vat_tax_id"] = tax.id

            if values:
                company.write(values)
