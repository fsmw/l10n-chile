from pathlib import Path
from unittest.mock import patch

from lxml import etree

from odoo import fields
from odoo.tests.common import mute_logger

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.l10n_cl_dte_signing.services.signature_service import SignatureService
from odoo.addons.l10n_cl_dte_signing.tests.common import SigningTestCase
from odoo.addons.l10n_cl_dte_xml.services.invoice_snapshot import (
    InvoiceSnapshotService,
)
from odoo.addons.l10n_cl_dte_xml.services.xml_composer import DteXmlComposer


class DteInvoiceTestCase(AccountTestInvoicingCommon, SigningTestCase):
    @classmethod
    def setUpClass(cls):
        # Odoo's account fixture emits this warning while loading its chart
        # template during a TransactionCase. It is unrelated to DTE behavior.
        with mute_logger("odoo.addons.account.models.chart_template"):
            super().setUpClass()
        if getattr(cls, "company_data", None):
            cls.company = cls.company_data["company"]
        for user in (cls.dte_manager, cls.dte_user):
            user.sudo().write(
                {
                    "company_id": cls.company.id,
                    "company_ids": [(4, cls.company.id)],
                }
            )
        cls.env = (
            cls.env["res.users"]
            .with_user(cls.dte_manager)
            .with_company(cls.company)
            .env
        )
        cls.company.sudo().write({"country_id": cls.env.ref("base.cl").id})
        cls.company.sudo().write({"currency_id": cls.env.ref("base.CLP").id})
        cls.company.sudo().write({"dte_sii_environment": "demo"})
        cls.company.partner_id.sudo().write(
            {
                "vat": "76864280-K",
                "street": "Avenida Prueba 100",
                "city": "Santiago",
                "name": "Emisor Sintetico de Pruebas",
            }
        )
        cls.company.sudo().write({"dte_sii_activity_code": "620200"})
        if "l10n_cl_activity_description" in cls.company._fields:
            cls.company.sudo().write(
                {"l10n_cl_activity_description": "Servicios informaticos"}
            )
        cls.chile = cls.env.ref("base.cl")
        cls.receiver = cls.env["res.partner"].create(
            {
                "name": "Receptor Sintetico",
                "vat": "76864280-K",
                "country_id": cls.chile.id,
                "street": "Calle Prueba 200",
                "city": "Santiago",
            }
        )
        cls.document_type_33 = cls.env["l10n_latam.document.type"].search(
            [("code", "=", "33"), ("country_id", "=", cls.chile.id)], limit=1
        )
        cls.document_type_34 = cls.env["l10n_latam.document.type"].search(
            [("code", "=", "34"), ("country_id", "=", cls.chile.id)], limit=1
        )
        cls.document_types = {
            code: cls.env["l10n_latam.document.type"].search(
                [("code", "=", code), ("country_id", "=", cls.chile.id)], limit=1
            )
            for code in ("33", "34", "39", "41", "56", "61")
        }
        cls.tax_19 = (
            cls.env["account.tax"]
            .sudo()
            .search(
                [
                    ("company_id", "=", cls.company.id),
                    ("type_tax_use", "=", "sale"),
                    ("amount_type", "=", "percent"),
                    ("amount", "=", 19),
                ],
                limit=1,
            )
        )
        if not cls.tax_19:
            cls.tax_19 = (
                cls.env["account.tax"]
                .sudo()
                .create(
                    {
                        "name": "Synthetic Chile VAT 19%",
                        "amount": 19,
                        "amount_type": "percent",
                        "type_tax_use": "sale",
                        "tax_group_id": cls.tax_sale_a.tax_group_id.id,
                        "company_id": cls.company.id,
                    }
                )
            )

    def make_invoice(self, code="33", post=True, taxable=True, partner=None):
        invoice = self.init_invoice(
            "out_invoice",
            partner=partner or self.receiver,
            invoice_date=fields.Date.from_string("2026-01-15"),
            post=False,
            products=[self.product_a],
            taxes=[self.tax_19] if taxable else [],
        )
        document_type = self.document_types[str(code)]
        if not document_type:
            raise AssertionError(f"Chilean document type {code} is not installed")
        invoice.write({"l10n_latam_document_type_id": document_type.id})
        if post:
            invoice.action_post()
        return invoice

    def capture(self, invoice, code=None, folio=100):
        code = code or invoice.l10n_latam_document_type_id.code
        authorization = self.validate_authorization(
            document_code=code,
            folio_start=folio,
            folio_end=folio + 3,
        )
        service = InvoiceSnapshotService()
        with patch.object(
            service, "_chile_timestamp", return_value="2026-01-15T12:00:00"
        ):
            snapshot = service.capture_invoice(invoice, authorization, folio, code)
        return snapshot, authorization

    def valid_invoice_snapshot(self, code="33", folio=100, taxable=True):
        invoice = self.make_invoice(code=code, taxable=taxable)
        return invoice, *self.capture(invoice, code=code, folio=folio)

    def validate_composed_xml(self, snapshot, authorization):
        snapshot["ted_xml"] = DteXmlComposer().compose_ted(snapshot, authorization)
        unsigned_xml = DteXmlComposer().compose_unsigned_document(snapshot)
        self.create_signing_profile()
        xml_bytes = SignatureService().sign_document(
            self.company.with_user(self.dte_manager), "demo", unsigned_xml
        )
        schema = etree.XMLSchema(
            etree.parse(str(Path(__file__).parents[1] / "static/xsd/DTE_v10.xsd"))
        )
        schema.assertValid(etree.fromstring(xml_bytes))
        return xml_bytes
