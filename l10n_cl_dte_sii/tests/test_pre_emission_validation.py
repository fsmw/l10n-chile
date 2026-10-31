# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import os
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytz

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from .real_dte.fixture_builder import REAL_DTE_DIR_ENV, RealDte, RealDteFixture

REAL_DTE_NAMES = (
    "dte-33-factura-odoo-account.move-884.xml",
    "dte-34-factura-exenta-odoo-account.move-1095.xml",
    "dte-61-nota-credito-odoo-account.move-718.xml",
)
CREDIT_NOTE_CODES = {"61", "112"}


@tagged("post_install", "-at_install")
class TestPreEmissionValidation(AccountTestInvoicingCommon):
    """Validate issuer, receiver and document data before building a DTE.

    Every rule has an allowed and a denied case. The three real documents the
    SII accepted are the oracle: none of the new rules may block them.
    """

    @classmethod
    @AccountTestInvoicingCommon.setup_country("cl")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.env.user.write(
            {"groups_id": [(4, cls.env.ref("l10n_cl_dte_sii.group_dte_manager").id)]}
        )
        cls.company.write(
            {
                "l10n_cl_dte_email": "dte@emisor.cl",
                "l10n_cl_dte_environment": "SIIDEMO",
            }
        )
        cls.partner = cls.partner_a
        cls.partner.write(
            {
                "country_id": cls.company.country_id.id,
                "vat": "76543210-3",
                "l10n_cl_sii_taxpayer_type": "1",
                "street": "Av. Siempre Viva 123",
                "city": "Santiago",
                "email": "receptor@example.cl",
                "l10n_cl_dte_email": "receptor@example.cl",
                "l10n_cl_activity_description": "Servicios de prueba",
            }
        )
        cls.journal = cls.company_data["default_journal_sale"]
        cls.revenue = cls.company_data["default_account_revenue"]
        cls.vat = cls.tax_sale_a
        cls.usd = cls.env.ref("base.USD")
        cls.usd.active = True
        ila_group = (
            cls.env["account.chart.template"]
            .with_company(cls.company)
            .ref("tax_group_ila")
        )
        cls.ila = cls.env["account.tax"].search(
            [
                ("company_id", "=", cls.company.id),
                ("type_tax_use", "=", "sale"),
                ("tax_group_id", "=", ila_group.id),
            ],
            limit=1,
        )
        assert cls.ila, "Falta un impuesto ILA de ventas en el plan chileno."

    def _chile_date(self, offset=0):
        return datetime.now(pytz.UTC).astimezone(
            pytz.timezone("America/Santiago")
        ).date() + timedelta(days=offset)

    def _make_move(self, code="33", taxes=None, currency=None, invoice_date=None):
        document_type = self.env["l10n_latam.document.type"].search(
            [("country_id.code", "=", "CL"), ("code", "=", code)], limit=1
        )
        self.assertTrue(document_type, f"Missing Chilean DTE type {code}")
        move_type = "out_refund" if code in CREDIT_NOTE_CODES else "out_invoice"
        return self.env["account.move"].create(
            {
                "move_type": move_type,
                "company_id": self.company.id,
                "journal_id": self.journal.id,
                "partner_id": self.partner.id,
                "invoice_date": invoice_date or self._chile_date(),
                "currency_id": (currency or self.company.currency_id).id,
                "l10n_latam_document_type_id": document_type.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Servicio de prueba",
                            "quantity": 1,
                            "price_unit": 1000,
                            "account_id": self.revenue.id,
                            "tax_ids": [(6, 0, (taxes or self.env["account.tax"]).ids)],
                        },
                    )
                ],
            }
        )

    def test_dte_email_rule_blocks_missing_issuer_email(self):
        self.company.sudo().l10n_cl_dte_email = False
        move = self._make_move()
        with self.assertRaisesRegex(ValidationError, "correo DTE del emisor"):
            move._l10n_cl_edi_post_validation()

    def test_dte_email_rule_blocks_missing_receiver_email(self):
        self.partner.write({"l10n_cl_dte_email": False, "email": False})
        move = self._make_move()
        with self.assertRaisesRegex(ValidationError, "correo DTE del receptor"):
            move._l10n_cl_edi_post_validation()

    def test_dte_email_rule_allows_export_without_emails(self):
        self.company.sudo().l10n_cl_dte_email = False
        self.partner.write({"l10n_cl_dte_email": False, "email": False})
        move = self._make_move(code="110")
        move._l10n_cl_edi_post_validation()

    def test_dte_email_rule_allows_boleta_without_emails(self):
        self.company.sudo().l10n_cl_dte_email = False
        self.partner.write({"l10n_cl_dte_email": False, "email": False})
        move = self._make_move(code="39", taxes=self.vat)
        move._l10n_cl_edi_post_validation()

    def _context_date(self, tz, offset=0, at=None):
        stamp = fields.Datetime.context_timestamp(
            self.env["account.move"].with_context(tz=tz),
            at or fields.Datetime.now(),
        )
        return stamp.date() + timedelta(days=offset)

    def test_stamp_date_rule_allows_today(self):
        move = self._make_move(invoice_date=self._chile_date(), taxes=self.vat)
        move._l10n_cl_edi_post_validation()

    def test_stamp_date_rule_blocks_future_invoice(self):
        move = self._make_move(
            invoice_date=self._context_date("UTC", 2), taxes=self.vat
        ).with_context(tz="UTC")
        with self.assertRaisesRegex(ValidationError, "fecha de timbre"):
            move._l10n_cl_edi_post_validation()

    def test_stamp_date_uses_the_record_timezone(self):
        at = datetime(2026, 10, 31, 1, 0)  # UTC: 22:00 the day before in Chile
        move = self._make_move(invoice_date=self._context_date("UTC", at=at))
        self.assertEqual(
            move.with_context(tz="UTC")._l10n_cl_stamp_date(at), date(2026, 10, 31)
        )
        self.assertEqual(
            move.with_context(tz="America/Santiago")._l10n_cl_stamp_date(at),
            date(2026, 10, 30),
        )

    def test_stamp_date_allows_a_document_dated_today_in_the_user_timezone(self):
        """Night in Chile, next day for the user: the document is still valid.

        The stamp is written in the record timezone, so an invoice dated today
        for a user in UTC must not be rejected while Chile is still yesterday.
        """
        at = datetime(2026, 10, 31, 1, 0)  # UTC: 22:00 the day before in Chile
        move = self._make_move(invoice_date=date(2026, 10, 31), taxes=self.vat)
        move = move.with_context(tz="UTC")
        with mock.patch.object(
            type(move),
            "_l10n_cl_stamp_date",
            autospec=True,
            side_effect=lambda record: fields.Datetime.context_timestamp(
                record, at
            ).date(),
        ):
            move._l10n_cl_edi_post_validation()

    def test_receiver_activity_rule_blocks_missing_activity(self):
        self.partner.l10n_cl_activity_description = False
        move = self._make_move()
        with self.assertRaisesRegex(ValidationError, "actividad económica"):
            move._l10n_cl_edi_post_validation()

    def test_receiver_activity_rule_allows_boleta(self):
        self.partner.l10n_cl_activity_description = False
        move = self._make_move(code="39", taxes=self.vat)
        move._l10n_cl_edi_post_validation()

    def test_receiver_address_rule_blocks_missing_street(self):
        self.partner.street = False
        move = self._make_move()
        with self.assertRaisesRegex(ValidationError, "dirección del receptor"):
            move._l10n_cl_edi_post_validation()

    def test_receiver_address_rule_allows_electronic_ticket(self):
        self.partner.street = False
        move = self._make_move(code="39", taxes=self.vat)
        move._l10n_cl_edi_post_validation()

    def test_boleta_39_blocks_withholdings(self):
        move = self._make_move(code="39", taxes=self.ila)
        with self.assertRaisesRegex(ValidationError, "ILA"):
            move._l10n_cl_edi_post_validation()

    def test_boleta_39_blocks_foreign_currency(self):
        move = self._make_move(code="39", taxes=self.vat, currency=self.usd)
        with self.assertRaisesRegex(ValidationError, "moneda de la compañía"):
            move._l10n_cl_edi_post_validation()

    def test_boleta_39_allows_clp_without_withholdings(self):
        self.partner.write(
            {
                "l10n_cl_dte_email": False,
                "email": False,
                "l10n_cl_activity_description": False,
                "street": False,
            }
        )
        self.company.sudo().l10n_cl_dte_email = False
        move = self._make_move(code="39", taxes=self.vat)
        move._l10n_cl_edi_post_validation()

    def test_exempt_document_blocks_taxes(self):
        move = self._make_move(code="34", taxes=self.vat)
        with self.assertRaisesRegex(ValidationError, "documento exento"):
            move._l10n_cl_edi_post_validation()

    def test_exempt_document_allows_no_taxes(self):
        move = self._make_move(code="34")
        move._l10n_cl_edi_post_validation()

    def test_exempt_rule_applies_to_every_exempt_type(self):
        for code in ("41", "110", "111", "112"):
            with self.subTest(code=code):
                move = self._make_move(code=code, taxes=self.vat)
                with self.assertRaisesRegex(ValidationError, "documento exento"):
                    move._l10n_cl_edi_post_validation()

    def test_taxed_33_requires_vat(self):
        move = self._make_move(code="33")
        with self.assertRaisesRegex(ValidationError, "ítems afectos"):
            move._l10n_cl_edi_post_validation()

    def test_taxed_33_allows_vat(self):
        move = self._make_move(code="33", taxes=self.vat)
        move._l10n_cl_edi_post_validation()

    def test_real_dtes_pass_pre_emission_validation(self):
        directory = os.environ.get(REAL_DTE_DIR_ENV)
        if not directory:
            self.skipTest(f"Definí {REAL_DTE_DIR_ENV} para validar los reales.")
        for name in REAL_DTE_NAMES:
            path = Path(directory) / "emitidos" / name
            if not path.is_file():
                self.skipTest(f"No está la muestra {path}.")
            with self.subTest(document=name):
                real = RealDte(path)
                fixture = RealDteFixture(self.env, real, path.parents[1] / "caf")
                fixture.configure_company(self.company)
                partner = fixture.create_partner()
                document_type = self.env["l10n_latam.document.type"].search(
                    [("country_id.code", "=", "CL"), ("code", "=", real.code)],
                    limit=1,
                )
                self.assertTrue(
                    document_type, f"Falta el tipo de documento {real.code}."
                )
                move = fixture.create_move(
                    self.company, partner, document_type, self.revenue
                )
                move._l10n_cl_edi_post_validation()
