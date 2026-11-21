# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_account.services.dte_issuance_adapter import (
    InvoiceIssuanceAdapter,
)

from ..services.liquidation_issuance_adapter import LiquidationIssuanceAdapter
from ..services.liquidation_xml_composer import LiquidationXmlComposer
from .common import NS, PRINCIPAL_VAT, LiquidationTestCase


@tagged("post_install", "-at_install")
class TestLiquidationIssue(LiquidationTestCase):
    """A principal's vendor bill issues a signed settlement invoice 43."""

    def _issue(self, bill, folio=500):
        self.validate_authorization(
            document_code="43", folio_start=folio, folio_end=folio + 5
        )
        self.create_signing_profile()
        return bill.with_user(self.dte_manager).issue_dte()

    def _text(self, node, path):
        return node.findtext(path, namespaces=NS)

    def test_taxable_commission_settles_sales_net_of_commission(self):
        bill = self.standard_settlement()
        # 120000 + 22800 VAT - (12000 + 2280 VAT) is owed to the principal.
        self.assertEqual(bill.amount_total, 128520)
        self.assertTrue(bill.dte_issuance_available)
        document = self._issue(bill)
        self.assertEqual(document.document_code, "43")
        self.assertEqual(bill.l10n_latam_document_number.lstrip("0"), "500")
        content, root = self.signed(document)
        composer = LiquidationXmlComposer()
        self.assertTrue(composer.validate_document(content, strict_sii=True))
        self.assertIsNone(root.find("s:Documento", NS))
        settlement = root.find("s:Liquidacion", NS)
        self.assertEqual(settlement.get("ID"), "F500T43")
        header = settlement.find("s:Encabezado", NS)
        self.assertEqual(self._text(header, "s:IdDoc/s:TipoDTE"), "43")
        self.assertEqual(self._text(header, "s:IdDoc/s:FmaPago"), "2")
        self.assertEqual(
            self._text(header, "s:Emisor/s:RUTEmisor"), self.company.partner_id.vat
        )
        self.assertIsNone(header.find("s:Emisor/s:CdgVendedor", NS))
        self.assertEqual(self._text(header, "s:Receptor/s:RUTRecep"), PRINCIPAL_VAT)
        self.assertEqual(self._text(header, "s:Receptor/s:GiroRecep"), "Agricultura")
        totals = header.find("s:Totales", NS)
        expected = {
            "s:MntNeto": "120000",
            "s:TasaIVA": "19.00",
            "s:IVA": "22800",
            "s:Comisiones/s:ValComNeto": "12000",
            "s:Comisiones/s:ValComIVA": "2280",
            "s:MntTotal": "128520",
        }
        for path, value in expected.items():
            self.assertEqual(self._text(totals, path), value, path)
        self.assertIsNone(totals.find("s:MntExe", NS))
        self.assertIsNone(totals.find("s:Comisiones/s:ValComExe", NS))
        details = settlement.findall("s:Detalle", NS)
        self.assertEqual(
            [self._text(line, "s:TpoDocLiq") for line in details], ["33", "39"]
        )
        self.assertEqual(self._text(details[0], "s:MontoItem"), "100000")
        charge = settlement.find("s:Comisiones", NS)
        self.assertEqual(
            [child.text for child in charge],
            ["1", "C", "Comision 10%", "12000", "0", "2280"],
        )
        # The Comisiones block sits between Referencia and TED.
        self.assertEqual(charge.getnext().tag, f"{{{NS['s']}}}TED")
        self.assertIn(b"<TD>43</TD>", content)
        self.assertIn(b"<MNT>128520</MNT>", content)
        self.assertIn(f"<RR>{PRINCIPAL_VAT}</RR>".encode(), content)

    def test_exempt_sale_and_exempt_other_charge(self):
        bill = self.settlement(
            [
                self.operation("Venta consignada", 1, 50000, self.purchase_vat),
                self.operation("Libros exentos", 2, 15000, code="34"),
                self.charge("Comision", 5000, self.sale_vat),
                self.charge("Flete exento", 3000, movement="O"),
            ]
        )
        # 50000 + 30000 + 9500 VAT - 5000 - 950 VAT - 3000.
        self.assertEqual(bill.amount_total, 80550)
        content, root = self.signed(self._issue(bill, folio=520))
        self.assertTrue(LiquidationXmlComposer().validate_document(content))
        totals = root.find("s:Liquidacion/s:Encabezado/s:Totales", NS)
        self.assertEqual(self._text(totals, "s:MntNeto"), "50000")
        self.assertEqual(self._text(totals, "s:MntExe"), "30000")
        self.assertEqual(self._text(totals, "s:IVA"), "9500")
        self.assertEqual(self._text(totals, "s:Comisiones/s:ValComNeto"), "5000")
        self.assertEqual(self._text(totals, "s:Comisiones/s:ValComExe"), "3000")
        self.assertEqual(self._text(totals, "s:Comisiones/s:ValComIVA"), "950")
        self.assertEqual(self._text(totals, "s:MntTotal"), "80550")
        exempt = root.findall("s:Liquidacion/s:Detalle", NS)[1]
        self.assertEqual(self._text(exempt, "s:TpoDocLiq"), "34")
        self.assertEqual(self._text(exempt, "s:IndExe"), "1")
        other = root.findall("s:Liquidacion/s:Comisiones", NS)[1]
        self.assertEqual(self._text(other, "s:TipoMovim"), "O")
        self.assertEqual(self._text(other, "s:ValComNeto"), "0")
        self.assertEqual(self._text(other, "s:ValComExe"), "3000")
        self.assertIsNone(other.find("s:ValComIVA", NS))

    def test_unsupported_bills_are_refused_before_a_folio_is_reserved(self):
        withholding = self._tax("Synthetic 19% WH", -19, "purchase", 15)
        sale = self.operation("Venta", 1, 10000, self.purchase_vat)
        commission = self.charge("Comision", 1000, self.sale_vat)
        cases = (
            ([sale], "flagged as a commission"),
            ([sale, {**commission, "price_unit": 1000}], "negative lines"),
            (
                [{**sale, "dte_settled_document_code": False}, commission],
                "document type",
            ),
            ([sale, self.charge("Comision", 1000, self.purchase_vat)], "or no VAT"),
            ([self.operation("Venta", 1, 10000, self.sale_vat), commission], "no VAT"),
            (
                [self.operation("Venta", 1, 10000, self.purchase_vat | withholding)]
                + [commission],
                "or no VAT",
            ),
            ([{**sale, "discount": 10}, commission], "line discounts"),
            ([{**sale, "price_unit": -10000}, commission], "positive lines"),
        )
        caf = self.validate_authorization(
            document_code="43", folio_start=600, folio_end=605
        )
        for lines, message in cases:
            with self.subTest(message=message):
                bill = self.settlement(lines + [self.operation("Base", 1, 50000)])
                with self.assertRaisesRegex(ValidationError, message):
                    bill.with_user(self.dte_manager).issue_dte()
                self.assertFalse(bill.dte_document_ids)
        self.assertFalse(
            self.env["l10n_cl.electronic.document"]
            .sudo()
            .search([("document_code", "=", "43")])
        )
        self.assertEqual(caf.state, "validated")

    def test_principal_needs_complete_fiscal_data(self):
        incomplete = self.principal.copy({"l10n_cl_activity_description": False})
        bill = self.standard_settlement(partner=incomplete)
        with self.assertRaisesRegex(ValidationError, "economic activity"):
            bill.with_user(self.dte_manager).issue_dte()
        own = self.principal.copy({"vat": self.company.partner_id.vat})
        with self.assertRaisesRegex(ValidationError, "cannot be the issuing"):
            self.standard_settlement(partner=own).with_user(
                self.dte_manager
            ).issue_dte()

    def test_43_is_routed_only_from_vendor_bills(self):
        bill = self.standard_settlement()
        self.assertIsInstance(
            bill._get_dte_issuance_adapter("43"), LiquidationIssuanceAdapter
        )
        self.assertEqual(bill._get_dte_allowed_move_types("43"), ("in_invoice",))
        self.assertTrue(self.document_types["43"]._is_doc_type_vendor())
        self.assertFalse(self.document_types["33"]._is_doc_type_vendor())
        invoice = self.make_invoice(code="33")
        self.assertTrue(invoice.dte_issuance_available)
        self.assertIs(
            type(invoice._get_dte_issuance_adapter("33")), InvoiceIssuanceAdapter
        )
        self.assertEqual(invoice._get_dte_allowed_move_types("33"), ("out_invoice",))
        customer_43 = self.settlement(
            [self.operation("Servicio", 1, 1000, self.sale_vat)],
            partner=self.receiver,
            move_type="out_invoice",
        )
        self.assertFalse(customer_43.dte_issuance_available)

    def test_composer_refuses_inconsistent_totals(self):
        bill = self.standard_settlement()
        content, _root = self.signed(self._issue(bill, folio=540))
        tampered = content.replace(b"<MntTotal>128520<", b"<MntTotal>142800<")
        with self.assertRaisesRegex(ValidationError, "totals differ"):
            LiquidationXmlComposer().validate_document(tampered)
