# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from lxml import etree

from odoo import Command, fields

from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase

NS = {"s": "http://www.sii.cl/SiiDte"}
PRINCIPAL_VAT = "11111111-1"


class LiquidationTestCase(DteInvoiceTestCase):
    """Synthetic commission agent settling consigned sales to its principal."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.document_types["43"] = cls.env["l10n_latam.document.type"].search(
            [("country_id", "=", cls.chile.id), ("code", "=", "43")], limit=1
        )
        cls.principal = cls.env["res.partner"].create(
            {
                "name": "Mandante Sintetico",
                "vat": PRINCIPAL_VAT,
                "country_id": cls.chile.id,
                "l10n_cl_sii_taxpayer_type": "1",
                "l10n_cl_activity_description": "Agricultura",
                "street": "Camino Rural 300",
                "city": "Talca",
            }
        )
        cls.purchase_vat = cls._tax("Synthetic purchase VAT 19%", 19, "purchase")
        cls.sale_vat = cls._tax("Synthetic sales VAT 19%", 19, "sale")

    @classmethod
    def _tax(cls, name, amount, tax_use, sii_code=14):
        return (
            cls.env["account.tax"]
            .sudo()
            .create(
                {
                    "name": name,
                    "amount": amount,
                    "amount_type": "percent",
                    "type_tax_use": tax_use,
                    "tax_group_id": cls.tax_purchase_a.tax_group_id.id,
                    "company_id": cls.company.id,
                    "l10n_cl_sii_code": sii_code,
                }
            )
        )

    def operation(self, name, quantity, price, taxes=None, code="33", **values):
        """Return the values of one sale made on behalf of the principal."""
        return {
            "name": name,
            "quantity": quantity,
            "price_unit": price,
            "tax_ids": [Command.set((taxes or self.env["account.tax"]).ids)],
            "dte_settled_document_code": code,
            **values,
        }

    def charge(self, name, amount, taxes=None, movement="C"):
        """Return the values of one agent commission or other charge."""
        return {
            "name": name,
            "quantity": 1,
            "price_unit": -amount,
            "tax_ids": [Command.set((taxes or self.env["account.tax"]).ids)],
            "dte_liquidation_charge": movement,
        }

    def settlement(self, lines, partner=None, code="43", move_type="in_invoice"):
        bill = self.env["account.move"].create(
            {
                "move_type": move_type,
                "partner_id": (partner or self.principal).id,
                "invoice_date": fields.Date.from_string("2026-01-15"),
                "invoice_line_ids": [Command.create(values) for values in lines],
            }
        )
        bill.l10n_latam_document_type_id = self.document_types[code]
        bill.action_post()
        return bill

    def standard_settlement(self, **kwargs):
        """Two consigned sales of 120000 net and a 10% taxable commission."""
        return self.settlement(
            [
                self.operation("Venta consignada", 10, 10000, self.purchase_vat),
                self.operation("Venta con boleta", 1, 20000, self.purchase_vat, "39"),
                self.charge("Comision 10%", 12000, self.sale_vat),
            ],
            **kwargs,
        )

    @staticmethod
    def signed(document):
        content = base64.b64decode(document.sudo()._get_signed_artifact().content)
        return content, etree.fromstring(content)

    def submission(self, document):
        return (
            self.env["l10n_cl.electronic.submission"]
            .sudo()
            .search([("document_id", "=", document.id)])
        )
