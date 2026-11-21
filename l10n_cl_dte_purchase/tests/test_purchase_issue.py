# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from unittest.mock import patch

from lxml import etree

from odoo import Command, fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_account.services.dte_issuance_adapter import (
    InvoiceIssuanceAdapter,
)
from odoo.addons.l10n_cl_dte_gateway.services.production_response_parser import (
    parse_upload,
)
from odoo.addons.l10n_cl_dte_gateway.services.production_transport import (
    ProductionSiiTransport,
)
from odoo.addons.l10n_cl_dte_gateway.tests.test_certification_transport import (
    upload_response,
)
from odoo.addons.l10n_cl_dte_gateway.tests.test_production_readiness import (
    TestDteProductionReadiness,
)
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import DteXmlComposer
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase

from ..services.purchase_issuance_adapter import PurchaseInvoiceIssuanceAdapter

NS = {"s": "http://www.sii.cl/SiiDte"}
SUPPLIER_VAT = "11111111-1"


@tagged("post_install", "-at_install")
class TestPurchaseIssue(DteInvoiceTestCase):
    """A vendor bill with total VAT retention issues a purchase invoice 46."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.document_types["46"] = cls.env["l10n_latam.document.type"].search(
            [("country_id", "=", cls.chile.id), ("code", "=", "46")], limit=1
        )
        cls.supplier = cls.env["res.partner"].create(
            {
                "name": "Vendedor Sintetico",
                "vat": SUPPLIER_VAT,
                "country_id": cls.chile.id,
                "l10n_cl_sii_taxpayer_type": "1",
                "l10n_cl_activity_description": "Agricultura",
                "street": "Camino Rural 300",
                "city": "Talca",
            }
        )
        cls.purchase_vat = cls._purchase_tax("Synthetic purchase VAT 19%", 19, 14)
        cls.total_retention = cls._purchase_tax("Synthetic 19% WH", -19, 15)

    @classmethod
    def _purchase_tax(cls, name, amount, sii_code):
        return (
            cls.env["account.tax"]
            .sudo()
            .create(
                {
                    "name": name,
                    "amount": amount,
                    "amount_type": "percent",
                    "type_tax_use": "purchase",
                    "tax_group_id": cls.tax_purchase_a.tax_group_id.id,
                    "company_id": cls.company.id,
                    "l10n_cl_sii_code": sii_code,
                }
            )
        )

    def _bill(self, lines, partner=None, code="46", move_type="in_invoice"):
        bill = self.env["account.move"].create(
            {
                "move_type": move_type,
                "partner_id": (partner or self.supplier).id,
                "invoice_date": fields.Date.from_string("2026-01-15"),
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": name,
                            "quantity": quantity,
                            "price_unit": price,
                            "tax_ids": [Command.set(taxes.ids)],
                        }
                    )
                    for name, quantity, price, taxes in lines
                ],
            }
        )
        bill.l10n_latam_document_type_id = self.document_types[code]
        bill.action_post()
        return bill

    def _retained_bill(self, **kwargs):
        taxes = self.purchase_vat | self.total_retention
        return self._bill([("Trigo", 10, 5000, taxes)], **kwargs)

    def _issue(self, bill, folio=500):
        self.validate_authorization(
            document_code="46", folio_start=folio, folio_end=folio + 5
        )
        self.create_signing_profile()
        return bill.with_user(self.dte_manager).issue_dte()

    def _signed(self, document):
        content = base64.b64decode(document.sudo()._get_signed_artifact().content)
        return content, etree.fromstring(content)

    def test_vendor_bill_issues_a_signed_46_with_total_retention(self):
        bill = self._retained_bill()
        self.assertEqual(bill.amount_total, 50000)
        self.assertTrue(bill.dte_issuance_available)
        document = self._issue(bill)
        self.assertEqual(document.document_code, "46")
        self.assertEqual(bill.l10n_latam_document_number.lstrip("0"), "500")
        content, root = self._signed(document)
        self.assertTrue(DteXmlComposer().validate_document(content))
        header = root.find("s:Documento/s:Encabezado", NS)
        self.assertEqual(header.findtext("s:IdDoc/s:TipoDTE", namespaces=NS), "46")
        self.assertEqual(
            header.findtext("s:Emisor/s:RUTEmisor", namespaces=NS),
            self.company.partner_id.vat,
        )
        self.assertEqual(
            header.findtext("s:Receptor/s:RUTRecep", namespaces=NS), SUPPLIER_VAT
        )
        self.assertIsNone(header.find("s:Emisor/s:CdgVendedor", NS))
        totals = header.find("s:Totales", NS)
        self.assertEqual(totals.findtext("s:MntNeto", namespaces=NS), "50000")
        self.assertEqual(totals.findtext("s:TasaIVA", namespaces=NS), "19.00")
        self.assertEqual(totals.findtext("s:IVA", namespaces=NS), "9500")
        retention = totals.find("s:ImptoReten", NS)
        self.assertEqual(retention.findtext("s:TipoImp", namespaces=NS), "15")
        self.assertEqual(retention.findtext("s:TasaImp", namespaces=NS), "19.00")
        self.assertEqual(retention.findtext("s:MontoImp", namespaces=NS), "9500")
        self.assertEqual(totals.findtext("s:MntTotal", namespaces=NS), "50000")
        detail = root.find("s:Documento/s:Detalle", NS)
        self.assertEqual(detail.findtext("s:CodImpAdic", namespaces=NS), "15")
        self.assertIn(b"<TD>46</TD>", content)
        self.assertIn(f"<RR>{SUPPLIER_VAT}</RR>".encode(), content)

    def test_exempt_line_is_reported_as_mntexe(self):
        taxes = self.purchase_vat | self.total_retention
        bill = self._bill(
            [
                ("Trigo", 10, 5000, taxes),
                ("Flete exento", 1, 7000, self.env["account.tax"]),
            ]
        )
        content, root = self._signed(self._issue(bill, folio=520))
        self.assertTrue(DteXmlComposer().validate_document(content, strict_sii=True))
        totals = root.find("s:Documento/s:Encabezado/s:Totales", NS)
        self.assertEqual(totals.findtext("s:MntNeto", namespaces=NS), "50000")
        self.assertEqual(totals.findtext("s:MntExe", namespaces=NS), "7000")
        self.assertEqual(totals.findtext("s:IVA", namespaces=NS), "9500")
        self.assertEqual(totals.findtext("s:MntTotal", namespaces=NS), "57000")
        self.assertEqual(bill.amount_total, 57000)
        exempt = root.findall("s:Documento/s:Detalle", NS)[1]
        self.assertEqual(exempt.findtext("s:IndExe", namespaces=NS), "1")
        self.assertIsNone(exempt.find("s:CodImpAdic", NS))

    def test_other_withholdings_are_refused(self):
        second_category = self._purchase_tax("Synthetic 2nd category", -15.25, 15)
        partial = self._purchase_tax("Synthetic partial retention", -8, 32)
        cases = (
            (self.purchase_vat | second_category, "total VAT retention"),
            (self.purchase_vat | partial, "Partial VAT retentions"),
            (self.purchase_vat, "must carry the total"),
            (self.total_retention, "only to lines with 19% VAT"),
        )
        for taxes, message in cases:
            with self.subTest(message=message):
                bill = self._bill([("Trigo", 1, 1000, taxes)])
                # Refused before a CAF folio is selected or reserved.
                with self.assertRaisesRegex(ValidationError, message):
                    bill.with_user(self.dte_manager).issue_dte()
                self.assertFalse(bill.dte_document_ids)

    def test_supplier_without_chilean_rut_is_refused(self):
        foreign = self.env["res.partner"].create(
            {
                "name": "Foreign Seller",
                "country_id": self.env.ref("base.ar").id,
                "l10n_cl_sii_taxpayer_type": "4",
                "street": "Calle 1",
                "city": "Mendoza",
            }
        )
        bill = self._retained_bill(partner=foreign)
        with self.assertRaisesRegex(ValidationError, "Supplier: enter a valid"):
            bill.with_user(self.dte_manager).issue_dte()

    def test_46_is_routed_only_from_vendor_bills(self):
        bill = self._retained_bill()
        self.assertIsInstance(
            bill._get_dte_issuance_adapter("46"), PurchaseInvoiceIssuanceAdapter
        )
        vendor_33 = self._bill([("Insumo", 1, 1000, self.purchase_vat)], code="33")
        self.assertFalse(vendor_33.dte_issuance_available)
        invoice = self.make_invoice(code="33")
        self.assertTrue(invoice.dte_issuance_available)
        adapter = invoice._get_dte_issuance_adapter("33")
        self.assertIs(type(adapter), InvoiceIssuanceAdapter)
        self.assertEqual(invoice._get_dte_allowed_move_types("46"), ("in_invoice",))
        self.assertEqual(invoice._get_dte_allowed_move_types("33"), ("out_invoice",))

    def test_production_upload_sends_the_46_in_an_envio_dte(self):
        TestDteProductionReadiness.configure_company_production(self)
        self.company.sudo().dte_sii_environment = "production"
        TestDteProductionReadiness.create_production_caf(
            self, code="46", folio_start=900
        )
        document = self._retained_bill().with_user(self.dte_manager).issue_dte()
        submission = (
            self.env["l10n_cl.electronic.submission"]
            .sudo()
            .search([("document_id", "=", document.id)])
        )
        receipt = parse_upload(
            upload_response(), self.company.vat, self.company.dte_sii_sender_vat
        )
        with patch.object(
            ProductionSiiTransport, "send_envelope", return_value=receipt
        ) as send:
            submission._job_send_to_sii()
        self.assertTrue(send.called, submission.sii_message)
        envelope = send.call_args.args[2]
        self.assertIn(b"<EnvioDTE", envelope)
        self.assertIn(b"<TpoDTE>46</TpoDTE>", envelope)
        self.assertEqual(submission.state, "submitted", submission.sii_message)
        self.assertEqual(document.fiscal_state, "submitted")
