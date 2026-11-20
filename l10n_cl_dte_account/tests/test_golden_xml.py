# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Golden-file tests for invoices 33 and 34.

Each test builds a posted invoice, issues it through the real flow (snapshot,
folio, TED, XML, signature) and compares the signed XML with a reviewed file in
``tests/golden/``. See ``l10n_cl_dte_xml/tests/golden.py`` for masking and
regeneration.
"""

import base64
from pathlib import Path
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_xml.services.invoice_snapshot import (
    InvoiceSnapshotService,
)
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase
from odoo.addons.l10n_cl_dte_xml.tests.golden import (
    FIXED_TIMESTAMP,
    assert_golden_xml,
)

GOLDEN_DIR = Path(__file__).parent / "golden"


@tagged("post_install", "-at_install")
class TestGoldenXml(DteInvoiceTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # SII requires GiroRecep on 33, 34 and 56; the generator emits it
        # from the receiver's activity description.
        cls.receiver.sudo().l10n_cl_activity_description = "Comercio al por menor"
        cls.dte_manager.sudo().name = "Synthetic DTE Seller"

    def issue(self, invoice, code, folio):
        self.validate_authorization(
            document_code=code, folio_start=folio, folio_end=folio + 5
        )
        self.create_signing_profile()
        with patch.object(
            InvoiceSnapshotService, "_chile_timestamp", return_value=FIXED_TIMESTAMP
        ):
            document = (
                self.env["account.move"]
                .with_user(self.dte_manager)
                .browse(invoice.id)
                .issue_dte()
            )
        return base64.b64decode(document._get_signed_artifact().content)

    def make_posted_invoice(
        self, code, lines, taxes, payment_form, days, currency=None
    ):
        payment_term = (
            self.env["account.payment.term"]
            .sudo()
            .create(
                {
                    "name": "Test DTE payment term",
                    "l10n_cl_sii_code": payment_form,
                    "line_ids": [
                        Command.create(
                            {"value": "percent", "value_amount": 100, "nb_days": days}
                        )
                    ],
                }
            )
        )
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.receiver.id,
                "invoice_date": fields.Date.from_string("2026-01-15"),
                "invoice_payment_term_id": payment_term.id,
                "currency_id": (currency or self.company.currency_id).id,
                "invoice_user_id": self.dte_manager.id,
                "l10n_latam_document_type_id": self.document_types[code].id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "product_id": self.env["product.product"]
                            .sudo()
                            .create({"name": name, "list_price": price})
                            .id,
                            "quantity": quantity,
                            "price_unit": price,
                            "discount": discount,
                            "tax_ids": [Command.set(taxes.ids)],
                        }
                    )
                    for name, quantity, price, discount in lines
                ],
            }
        )
        invoice.action_post()
        return invoice

    def test_invoice_33_two_lines_with_discount(self):
        invoice = self.make_posted_invoice(
            "33",
            [
                ("Arriendo de equipo", 3, 12500, 0),
                ("Servicio de soporte", 1, 40000, 10),
            ],
            self.tax_19,
            payment_form="2",
            days=30,
        )
        assert_golden_xml(
            self, self.issue(invoice, "33", 700), GOLDEN_DIR / "dte_33_basic.xml"
        )

    def test_exempt_invoice_34_two_lines_with_discount(self):
        invoice = self.make_posted_invoice(
            "34",
            [
                ("Asesoria educacional", 2, 30000, 0),
                ("Curso de capacitacion", 1, 50000, 20),
            ],
            self.env["account.tax"],
            payment_form="1",
            days=0,
        )
        assert_golden_xml(
            self, self.issue(invoice, "34", 710), GOLDEN_DIR / "dte_34_exempt.xml"
        )

    def _sale_tax(self, name, amount, sii_code):
        return (
            self.env["account.tax"]
            .sudo()
            .create(
                {
                    "name": name,
                    "amount": amount,
                    "amount_type": "percent",
                    "type_tax_use": "sale",
                    "tax_group_id": self.tax_19.tax_group_id.id,
                    "company_id": self.company.id,
                    "l10n_cl_sii_code": sii_code,
                }
            )
        )

    def test_invoice_33_with_additional_tax(self):
        ila = self._sale_tax("Synthetic ILA 10%", 10, 27)
        invoice = self.make_posted_invoice(
            "33",
            [("Bebida analcoholica", 24, 1000, 0)],
            self.tax_19 | ila,
            payment_form="1",
            days=0,
        )
        self.assertEqual(invoice.amount_total, 24000 + 4560 + 2400)
        assert_golden_xml(
            self,
            self.issue(invoice, "33", 720),
            GOLDEN_DIR / "dte_33_additional_tax.xml",
        )

    def test_vat_withholding_is_not_issued_as_vat(self):
        withholding = self._sale_tax("Synthetic VAT withholding", -19, 15)
        invoice = self.make_posted_invoice(
            "33",
            [("Servicio", 1, 10000, 0)],
            self.tax_19 | withholding,
            payment_form="1",
            days=0,
        )
        with self.assertRaisesRegex(ValidationError, "19% VAT"):
            self.issue(invoice, "33", 730)

    def test_invoice_33_cites_a_purchase_order(self):
        invoice = self.make_posted_invoice(
            "33",
            [("Servicio de soporte", 1, 40000, 0)],
            self.tax_19,
            payment_form="2",
            days=30,
        )
        purchase_order = self.env["l10n_latam.document.type"].search(
            [("code", "=", "801"), ("country_id.code", "=", "CL")], limit=1
        )
        invoice.dte_reference_ids = [
            Command.create(
                {
                    "document_type_id": purchase_order.id,
                    "folio": "OC-4500123",
                    "date": fields.Date.from_string("2026-01-10"),
                    "reason": "Orden de compra del cliente",
                }
            )
        ]
        assert_golden_xml(
            self,
            self.issue(invoice, "33", 740),
            GOLDEN_DIR / "dte_33_purchase_order.xml",
        )
        with self.assertRaises(UserError):
            invoice.dte_reference_ids.reason = "Otra"

    def test_invoice_33_with_global_discount(self):
        invoice = self.make_posted_invoice(
            "33",
            [
                ("Arriendo de equipo", 2, 50000, 0),
                ("Descuento por volumen", 1, -10000, 0),
            ],
            self.tax_19,
            payment_form="1",
            days=0,
        )
        self.assertEqual(invoice.amount_untaxed, 90000)
        signed = self.issue(invoice, "33", 750)
        assert_golden_xml(self, signed, GOLDEN_DIR / "dte_33_global_discount.xml")
        self.assertEqual(signed.count(b"<Detalle>"), 1)

    def test_invoice_33_in_us_dollars(self):
        usd = self.env.ref("base.USD").sudo()
        usd.active = True
        self.env["res.currency.rate"].sudo().create(
            {
                "name": "2026-01-01",
                "currency_id": usd.id,
                "company_id": self.company.id,
                "rate": 1 / 950,
            }
        )
        invoice = self.make_posted_invoice(
            "33",
            [("Licencia anual", 2, 100, 0)],
            self.tax_19,
            payment_form="2",
            days=30,
            currency=usd,
        )
        self.assertEqual(invoice.amount_total_signed, 226100)
        assert_golden_xml(
            self, self.issue(invoice, "33", 760), GOLDEN_DIR / "dte_33_usd.xml"
        )
