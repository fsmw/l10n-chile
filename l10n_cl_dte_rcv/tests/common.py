# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from collections import defaultdict

from odoo import Command
from odoo.tests import TransactionCase

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from ..services.rcv_normalizer import RcvNormalizer
from ..services.rcv_values import normalize_vat

# Synthetic RUTs with valid check digits; they do not identify anyone.
COMPANY_RUT = "76111111-6"
SUPPLIER_RUT = "76222222-1"
OTHER_SUPPLIER_RUT = "77333333-5"
CUSTOMER_RUT = "12345678-5"
CSV_HEADER = (
    "tipo_documento,rut_contraparte,razon_social,folio,fecha,"
    "neto,exento,iva,iva_no_recuperable,iva_uso_comun,total"
)


class RcvTestCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.vat = COMPANY_RUT
        cls.group_user = cls.env.ref("l10n_cl_dte_core.group_dte_user")
        cls.group_manager = cls.env.ref("l10n_cl_dte_core.group_dte_manager")
        cls.manager = cls._user("rcv-test-manager", cls.group_manager)
        cls.reader = cls._user("rcv-test-reader", cls.group_user)

    @classmethod
    def _user(cls, login, group, companies=None):
        companies = companies or cls.company
        return cls.env["res.users"].create(
            {
                "name": login,
                "login": login,
                "company_id": companies[:1].id,
                "company_ids": [(6, 0, companies.ids)],
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id, group.id])],
            }
        )

    def _period(self, operation="purchase", year=2026, month=8, company=None):
        return self.env["l10n_cl.dte.rcv.period"].create(
            {
                "company_id": (company or self.company).id,
                "year": year,
                "month": month,
                "operation": operation,
            }
        )

    def _sync(self, period, source="sii"):
        return self.env["l10n_cl.dte.rcv.sync"].create(
            {
                "period_id": period.id,
                "company_id": period.company_id.id,
                "operation": period.operation,
                "source": source,
            }
        )

    @staticmethod
    def _csv(*lines, header=CSV_HEADER):
        return "\n".join((header, *lines)).encode("utf-8")

    def _wizard(self, content, operation="purchase", month=8, user=None):
        env = self.env(user=user or self.manager)
        return env["l10n_cl.dte.rcv.csv.import.wizard"].create(
            {
                "company_id": self.company.id,
                "year": 2026,
                "month": month,
                "operation": operation,
                "upload": base64.b64encode(content),
                "filename": "rcv.csv",
            }
        )


class RcvMatchingTestCase(AccountTestInvoicingCommon):
    """Chilean chart of accounts, partners and RCV reads for reconciliation."""

    @classmethod
    @AccountTestInvoicingCommon.setup_country("cl")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.group_user = cls.env.ref("l10n_cl_dte_core.group_dte_user")
        cls.group_manager = cls.env.ref("l10n_cl_dte_core.group_dte_manager")
        # The test user is an accounting manager; it also manages DTEs.
        cls.env.user.sudo().groups_id |= cls.group_manager
        cls.company.sudo().write({"vat": COMPANY_RUT, "dte_sii_environment": "demo"})
        cls.company.partner_id.sudo().l10n_cl_sii_taxpayer_type = "1"
        invoicing = cls.env.ref("account.group_account_invoice")
        cls.manager = cls._matching_user(
            "rcv-match-manager", cls.group_manager | invoicing
        )
        cls.reader = cls._matching_user("rcv-match-reader", cls.group_user)
        cls.supplier = cls._cl_partner("Fake Supplier", SUPPLIER_RUT)
        cls.customer = cls._cl_partner("Fake Customer", CUSTOMER_RUT)
        chart = cls.env["account.chart.template"].with_company(cls.company)
        cls.purchase_vat = chart.ref("OTAX_19")
        cls.sale_vat = chart.ref("ITAX_19")
        cls.document_types = {
            code: cls.env["l10n_latam.document.type"].search(
                [("code", "=", code), ("country_id.code", "=", "CL")], limit=1
            )
            for code in ("33", "34", "56", "61", "110")
        }

    @classmethod
    def _matching_user(cls, login, groups, companies=None):
        companies = companies or cls.company
        return cls.env["res.users"].create(
            {
                "name": login,
                "login": login,
                "company_id": companies[:1].id,
                "company_ids": [Command.set(companies.ids)],
                "groups_id": [
                    Command.set((cls.env.ref("base.group_user") | groups).ids)
                ],
            }
        )

    @classmethod
    def _cl_partner(cls, name, vat):
        return cls.env["res.partner"].create(
            {
                "name": name,
                "is_company": True,
                "vat": vat,
                "country_id": cls.env.ref("base.cl").id,
                "l10n_latam_identification_type_id": cls.env.ref("l10n_cl.it_RUT").id,
                "l10n_cl_sii_taxpayer_type": "1",
            }
        )

    def _bill(self, folio, net=1000, taxed=True, date="2026-08-12", **kwargs):
        """Create a vendor bill with one line; post it unless ``post=False``."""
        values = {
            "move_type": kwargs.get("move_type", "in_invoice"),
            "partner_id": kwargs.get("partner", self.supplier).id,
            "invoice_date": date,
            "date": date,
            "journal_id": kwargs.get(
                "journal", self.company_data["default_journal_purchase"]
            ).id,
            "l10n_latam_document_type_id": self.document_types[
                kwargs.get("code", "33")
            ].id,
            "l10n_latam_document_number": folio,
            "invoice_line_ids": [
                Command.create(
                    {
                        "name": "Synthetic purchase",
                        "quantity": 1,
                        "price_unit": net,
                        "tax_ids": [
                            Command.set(self.purchase_vat.ids if taxed else [])
                        ],
                    }
                )
            ],
        }
        move = self.env["account.move"].create(values)
        if kwargs.get("post", True):
            move.action_post()
        return move

    def _invoice(self, net=1000, date="2026-08-12", partner=None, code="33"):
        """Create and post a customer invoice; its folio comes from the journal."""
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": (partner or self.customer).id,
                "invoice_date": date,
                "date": date,
                "journal_id": self.company_data["default_journal_sale"].id,
                "l10n_latam_document_type_id": self.document_types[code].id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Synthetic sale",
                            "quantity": 1,
                            "price_unit": net,
                            "tax_ids": [Command.set(self.sale_vat.ids)],
                        }
                    )
                ],
            }
        )
        move.action_post()
        return move

    @staticmethod
    def _row(folio, vat, net=1000, vat_amount=190, code=33, **kwargs):
        """Return one SII detail row; ``total`` defaults to net plus VAT."""
        number, digit = normalize_vat(vat).split("-")
        exempt = kwargs.get("exempt", 0)
        return {
            "detTipoDoc": code,
            "detNroDoc": folio,
            "detRutDoc": int(number),
            "detDvDoc": digit,
            "detRznSoc": "Fake Counterpart",
            "detFchDoc": kwargs.get("date", "2026-08-12"),
            "detMntNeto": net,
            "detMntExe": exempt,
            "detMntIVA": vat_amount,
            "detMntIVANoRec": kwargs.get("vat_not_recoverable", 0),
            "detIVAUsoComun": kwargs.get("common_vat", 0),
            "detMntTotal": kwargs.get("total", net + exempt + vat_amount),
        }

    @staticmethod
    def _section(rows, state="REGISTRO", complete=True):
        """Shape detail rows as one SII section whose summary proves them."""
        details = defaultdict(list)
        for row in rows:
            details[str(row["detTipoDoc"])].append(row)
        summary = [
            {
                "rsmnTipoDocInteger": int(type_code),
                "rsmnTotDoc": len(items),
                "rsmnMntNeto": sum(item["detMntNeto"] for item in items),
                "rsmnMntExe": sum(item["detMntExe"] for item in items),
                "rsmnMntIVA": sum(item["detMntIVA"] for item in items),
                "rsmnMntTotal": sum(item["detMntTotal"] for item in items),
            }
            for type_code, items in details.items()
        ]
        section = {
            "state": state,
            "summary": {"data": summary},
            "details": dict(details),
        }
        if not complete:
            section["unavailable_details"] = list(details)
        return section

    def _read(self, sections, operation="purchase", month=8, env=None):
        """Store ``sections`` as a new SII read of the period; return the read."""
        env = env or self.env
        values = {
            "company_id": self.company.id,
            "year": 2026,
            "month": month,
            "operation": operation,
        }
        period_model = env["l10n_cl.dte.rcv.period"]
        period = period_model.search(
            [(key, "=", value) for key, value in values.items()]
        ) or period_model.create(values)
        sync = env["l10n_cl.dte.rcv.sync"].create(
            {
                "period_id": period.id,
                "company_id": self.company.id,
                "operation": operation,
            }
        )
        RcvNormalizer(env).apply(sync, sections)
        return sync

    def _document(self, sync, folio):
        return sync.document_ids.filtered(lambda document: document.folio == str(folio))
