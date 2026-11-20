# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import patch

from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_xml.tests.common import DteInvoiceTestCase

from ..services.export_snapshot import ExportSnapshotService
from ..services.export_xml_composer import ExportXmlComposer

NS = {"sii": "http://www.sii.cl/SiiDte"}


@tagged("post_install", "-at_install")
class TestExportXml(DteInvoiceTestCase):
    """A 110 export is captured, composed, signed and checked against the SII."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.document_types["110"] = cls.env["l10n_latam.document.type"].search(
            [("country_id", "=", cls.chile.id), ("code", "=", "110")], limit=1
        )
        cls.usd = cls.env.ref("base.USD")
        cls.usd.sudo().active = True
        cls.usa = cls.env.ref("base.us")
        cls.buyer = cls.env["res.partner"].create(
            {
                "name": "Buyer Inc",
                "is_company": True,
                "street": "100 Main Street",
                "city": "Miami",
                "country_id": cls.usa.id,
            }
        )
        cls.catalog = {
            kind: cls._catalog(kind, code)
            for kind, code in (
                ("payment", "1"),
                ("sale_mode", "1"),
                ("sale_clause", "5"),
                ("transport", "1"),
                ("port", "906"),
                ("unit", "6"),
                ("package", "13"),
            )
        }

    @classmethod
    def _catalog(cls, kind, code):
        return (
            cls.env["l10n_cl.dte.export.catalog"]
            .sudo()
            .create(
                {
                    "kind": kind,
                    "code": code,
                    "name": f"{kind} {code}",
                    "source_url": "https://www.aduana.cl/",
                    "source_date": "2026-01-01",
                }
            )
        )

    def _export(self, currency=None, **values):
        invoice = self.init_invoice(
            "out_invoice",
            partner=self.buyer,
            invoice_date="2026-01-15",
            products=[self.product_a],
            taxes=[],
            currency=currency or self.usd,
        )
        invoice.invoice_line_ids.write(
            {"quantity": 2, "price_unit": 1234.5, "tax_ids": [(5, 0, 0)]}
        )
        catalog = self.catalog
        invoice.write(
            {
                "l10n_latam_document_type_id": self.document_types["110"].id,
                "dte_export_service": "goods",
                "dte_export_receiver_id": "EIN-12-3456789",
                "dte_export_nationality_id": self.usa.id,
                "dte_export_destination_id": self.usa.id,
                "dte_export_rate": 950.25,
                "dte_export_rate_date": "2026-01-15",
                "dte_export_rate_source": "BCCh observed dollar 2026-01-15",
                "dte_export_payment_id": catalog["payment"].id,
                "dte_export_sale_mode_id": catalog["sale_mode"].id,
                "dte_export_sale_clause_id": catalog["sale_clause"].id,
                "dte_export_clause_total": 2469,
                "dte_export_transport_id": catalog["transport"].id,
                "dte_export_loading_port_id": catalog["port"].id,
                "dte_export_gross_weight": 120.5,
                "dte_export_gross_unit_id": catalog["unit"].id,
                "dte_export_package_ids": [
                    (0, 0, {"type_id": catalog["package"].id, "quantity": 4})
                ],
                "dte_export_reference_ids": [
                    (
                        0,
                        0,
                        {
                            "document_type": "807",
                            "folio": "6001234567",
                            "date": "2026-01-14",
                        },
                    )
                ],
                **values,
            }
        )
        invoice.action_post()
        return invoice

    def _signed(self, invoice):
        caf = self.validate_authorization(
            document_code="110", folio_start=50, folio_end=55
        )
        service = ExportSnapshotService()
        with patch(
            "odoo.addons.l10n_cl_dte_xml.services.invoice_snapshot."
            "InvoiceSnapshotService._chile_timestamp",
            return_value="2026-01-15T12:00:00",
        ):
            snapshot = service.capture(invoice, caf, 50, "110")
        composer = ExportXmlComposer()
        snapshot["ted_xml"] = composer.compose_ted(snapshot, caf)
        unsigned = composer.compose_unsigned_document(snapshot)
        self.create_signing_profile()
        signed = SignatureService().sign_document(
            self.company.with_user(self.dte_manager), "demo", unsigned
        )
        composer.validate_document(signed)
        return snapshot, etree.fromstring(signed)

    def test_goods_export_in_dollars_is_a_valid_signed_110(self):
        snapshot, root = self._signed(self._export())
        self.assertEqual(snapshot["totals"]["total"], "2469.00")
        self.assertEqual(snapshot["other_currency"]["total"], "2346167")

        def text(path):
            return root.findtext(path, namespaces=NS)

        header = "sii:Exportaciones/sii:Encabezado/"
        self.assertEqual(text(header + "sii:Receptor/sii:RUTRecep"), "55555555-5")
        self.assertIsNone(root.find(".//sii:Extranjero/sii:NumId", NS))
        self.assertEqual(text(header + "sii:Totales/sii:TpoMoneda"), "DOLAR USA")
        self.assertEqual(text(header + "sii:OtraMoneda/sii:TpoMoneda"), "PESO CL")
        customs = header + "sii:Transporte/sii:Aduana/"
        self.assertEqual(text(customs + "sii:CodClauVenta"), "5")
        self.assertEqual(text(customs + "sii:TotBultos"), "4")
        self.assertEqual(
            text(customs + "sii:CodPaisDestin"), self.usa.l10n_cl_customs_code
        )
        self.assertEqual(text(".//sii:Referencia/sii:TpoDocRef"), "807")
        self.assertEqual(text(".//sii:TED/sii:DD/sii:MNT"), "2469.00")

    def test_service_export_in_pesos_needs_its_supporting_reference(self):
        clp = self.env.ref("base.CLP")
        values = {
            "dte_export_service": "3",
            "dte_export_sale_clause_id": False,
            "dte_export_clause_total": 0,
            "dte_export_reference_ids": [(5, 0, 0)],
            "dte_export_package_ids": [(5, 0, 0)],
            "dte_export_gross_weight": 0,
        }
        invoice = self._export(currency=clp, **values)
        with self.assertRaises(ValidationError):
            ExportSnapshotService().validate_source(invoice, "110")
        invoice.button_draft()
        invoice.write(
            {
                "dte_export_reference_ids": [
                    (
                        0,
                        0,
                        {"document_type": "812", "folio": "77", "date": "2026-01-10"},
                    )
                ]
            }
        )
        invoice.action_post()
        snapshot, root = self._signed(invoice)
        self.assertEqual(snapshot["other_currency"]["rate"], "1")
        self.assertEqual(
            root.findtext(".//sii:IdDoc/sii:IndServicio", namespaces=NS), "3"
        )

    def test_missing_customs_data_and_taxes_are_refused(self):
        service = ExportSnapshotService()
        for values in (
            {"dte_export_sale_clause_id": False},
            {"dte_export_receiver_id": False},
            {"dte_export_rate": 0},
            {"dte_export_gross_unit_id": False},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                service.validate_source(self._export(**values), "110")
        with self.assertRaises(ValidationError):
            service.validate_source(self._export(), "111")
