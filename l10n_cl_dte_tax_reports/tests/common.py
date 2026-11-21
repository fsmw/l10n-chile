# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import Command
from odoo.tests.common import mute_logger

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

COMPANY_RUT = "76111111-6"
CUSTOMER_RUT = "76222222-1"
SUPPLIER_RUT = "76333333-7"
FEE_ISSUER_RUT = "15555555-6"
DOCUMENT_CODES = ("33", "34", "39", "46", "56", "61", "71", "110")


class TaxReportsTestCase(AccountTestInvoicingCommon):
    """Chilean chart of accounts with synthetic partners and documents."""

    @classmethod
    @AccountTestInvoicingCommon.setup_country("cl")
    def setUpClass(cls):
        with mute_logger("odoo.addons.account.models.chart_template"):
            super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.company.write({"vat": COMPANY_RUT})
        cls.company.partner_id.l10n_cl_sii_taxpayer_type = "1"
        cls.customer = cls._cl_partner("Synthetic Customer", CUSTOMER_RUT, "1")
        cls.supplier = cls._cl_partner("Synthetic Supplier", SUPPLIER_RUT, "1")
        cls.fee_issuer = cls._cl_partner("Synthetic Fee Issuer", FEE_ISSUER_RUT, "2")
        chart = cls.env["account.chart.template"].with_company(cls.company)
        cls.sale_vat = chart.ref("ITAX_19")
        cls.purchase_vat = chart.ref("OTAX_19")
        cls.purchase_vat_not_recoverable = chart.ref("iva_compra_no_recup")
        cls.purchase_vat_common_use = chart.ref("iva_compra_uso_comun")
        cls.vat_total_retention = chart.ref("I_RTI")
        cls.second_category = chart.ref("I_IR2C_2026")
        cls.document_types = {
            code: cls.env["l10n_latam.document.type"].search(
                [("code", "=", code), ("country_id.code", "=", "CL")], limit=1
            )
            for code in DOCUMENT_CODES
        }
        cls.folio = 1000

    @classmethod
    def _cl_partner(cls, name, vat, taxpayer_type):
        return cls.env["res.partner"].create(
            {
                "name": name,
                "is_company": True,
                "vat": vat,
                "country_id": cls.env.ref("base.cl").id,
                "l10n_latam_identification_type_id": cls.env.ref("l10n_cl.it_RUT").id,
                "l10n_cl_sii_taxpayer_type": taxpayer_type,
            }
        )

    @classmethod
    def _document(cls, move_type, code, price, taxes, date="2026-08-12", **kwargs):
        """Create and post one single-line invoice of the given document type."""
        sale = move_type.startswith("out_")
        journal = cls.company_data[
            "default_journal_sale" if sale else "default_journal_purchase"
        ]
        values = {
            "move_type": move_type,
            "partner_id": kwargs.get(
                "partner", cls.customer if sale else cls.supplier
            ).id,
            "invoice_date": date,
            "date": date,
            "journal_id": journal.id,
            "l10n_latam_document_type_id": cls.document_types[code].id,
            "invoice_line_ids": [
                Command.create(
                    {
                        "name": "Synthetic line",
                        "quantity": 1,
                        "price_unit": price,
                        "tax_ids": [Command.set(taxes.ids)],
                    }
                )
            ],
        }
        if not sale and code != "46":
            cls.folio += 1
            values["l10n_latam_document_number"] = str(cls.folio)
        move = cls.env["account.move"].create(values)
        move.action_post()
        return move

    @classmethod
    def _entry(cls, date, lines, company=None):
        """Post a miscellaneous entry from ``(account, debit, credit)`` lines."""
        company = company or cls.company
        journal = cls.env["account.journal"].search(
            [("company_id", "=", company.id), ("type", "=", "general")], limit=1
        )
        move = cls.env["account.move"].create(
            {
                "move_type": "entry",
                "date": date,
                "journal_id": journal.id,
                "line_ids": [
                    Command.create(
                        {
                            "name": "Synthetic",
                            "account_id": account.id,
                            "debit": debit,
                            "credit": credit,
                        }
                    )
                    for account, debit, credit in lines
                ],
            }
        )
        move.action_post()
        return move

    @classmethod
    def _user(cls, login, group_xmlid, companies=None):
        companies = companies or cls.company
        return cls.env["res.users"].create(
            {
                "name": login,
                "login": login,
                "company_id": companies[:1].id,
                "company_ids": [Command.set(companies.ids)],
                "groups_id": [
                    Command.set(
                        [cls.env.ref("base.group_user").id, cls.env.ref(group_xmlid).id]
                    )
                ],
            }
        )
