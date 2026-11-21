# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Notes on invoices with SII additional taxes and global discounts.

The synthetic invoice used here has a beer line with VAT and ILA (code 26,
20.5%), a taxable service line, an exempt freight line, a taxable global
discount of 2000 and an exempt global discount of 300:

* line 1: 10000, discount share 1000, net 9000, VAT 1710, ILA 2050
* line 2: 10000, discount share 1000, net 9000, VAT 1710
* line 3: 3000 exempt, discount share 300, exempt 2700
* totals: net 18000, exempt 2700, VAT 3420, ILA 2050, total 26170
"""

import base64
from pathlib import Path

from lxml import etree

from odoo import Command, fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.l10n_cl_dte_xml.services.xml_composer import SII_NAMESPACE
from odoo.addons.l10n_cl_dte_xml.tests.golden import (
    assert_golden_xml,
    frozen_chile_now,
)

from ..services.note_reference_service import NoteReferenceService
from ..services.note_xml_composer import NoteXmlComposer
from .common import DteNoteTestCase

GOLDEN_DIR = Path(__file__).parent / "golden"
NOTE_SNAPSHOT_MODULE = "odoo.addons.l10n_cl_dte_note.services.note_snapshot"
NS = f"{{{SII_NAMESPACE}}}"


@tagged("post_install", "-at_install")
class TestNoteAdditionalTaxes(DteNoteTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dte_manager.sudo().name = "Synthetic DTE Seller"
        cls.receiver.sudo().l10n_cl_activity_description = "Comercio al por menor"
        cls.ila = (
            cls.env["account.tax"]
            .sudo()
            .create(
                {
                    "name": "Synthetic ILA beer 20.5%",
                    "amount": 20.5,
                    "amount_type": "percent",
                    "type_tax_use": "sale",
                    "tax_group_id": cls.tax_19.tax_group_id.id,
                    "company_id": cls.company.id,
                    "l10n_cl_sii_code": 26,
                }
            )
        )

    # Helpers -------------------------------------------------------------

    def make_source(self, lines):
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.receiver.id,
                "invoice_date": fields.Date.from_string("2026-01-15"),
                "invoice_user_id": self.dte_manager.id,
                "l10n_latam_document_type_id": self.document_types["33"].id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "product_id": self.env["product.product"]
                            .sudo()
                            .create({"name": name, "list_price": abs(price)})
                            .id,
                            "quantity": quantity,
                            "price_unit": price,
                            "tax_ids": [Command.set(taxes.ids)],
                        }
                    )
                    for name, quantity, price, taxes in lines
                ],
            }
        )
        invoice.action_post()
        return self.create_accepted_source("33", move=invoice)

    def mixed_source(self):
        return self.make_source(
            [
                ("Cerveza artesanal", 10, 1000, self.tax_19 | self.ila),
                ("Servicio de despacho", 2, 5000, self.tax_19),
                ("Flete exento", 1, 3000, self.env["account.tax"]),
                ("Descuento global", 1, -2000, self.tax_19),
                ("Descuento exento", 1, -300, self.env["account.tax"]),
            ]
        )

    def ila_source(self):
        return self.make_source(
            [
                ("Cerveza artesanal", 10, 1000, self.tax_19 | self.ila),
                ("Servicio de despacho", 2, 5000, self.tax_19),
            ]
        )

    def issue(self, note):
        with frozen_chile_now(NOTE_SNAPSHOT_MODULE):
            document = note.with_user(self.dte_manager).issue_dte()
        return document, base64.b64decode(document._get_signed_artifact().content)

    def prepare_issuance(self, code="61", folio=800, signing=True):
        self.validate_authorization(
            document_code=code, folio_start=folio, folio_end=folio + 9
        )
        if signing:
            self.create_signing_profile()

    def partial_credit(self, source, target, quantities, discounts=()):
        """Credit ``quantities`` {ordinal: qty} with the given discount lines."""
        note = self.create_credit_note(source, target, operation="return")
        commands = []
        for line in note.invoice_line_ids.filtered(
            lambda item: item.display_type == "product"
        ):
            ordinal = line.dte_note_origin_line_number
            if ordinal in quantities:
                commands.append(
                    Command.update(line.id, {"quantity": quantities[ordinal]})
                )
            elif ordinal or line.price_subtotal >= 0:
                commands.append(Command.delete(line.id))
        discount_lines = note.invoice_line_ids.filtered(
            lambda item: item.display_type == "product" and item.price_subtotal < 0
        )
        for line, amount in zip(discount_lines, list(discounts), strict=False):
            commands.append(Command.update(line.id, {"price_unit": -amount}))
        for line in discount_lines[len(discounts) :]:
            commands.append(Command.delete(line.id))
        note.write({"invoice_line_ids": commands})
        note.invoice_user_id = self.dte_manager
        note.action_post()
        return note

    @staticmethod
    def totals(xml_bytes):
        node = etree.fromstring(xml_bytes).find(f".//{NS}Totales")
        result = {
            child.tag.removeprefix(NS): child.text
            for child in node
            if child.tag != f"{NS}ImptoReten"
        }
        result["ImptoReten"] = [
            (
                tax.findtext(f"{NS}TipoImp"),
                tax.findtext(f"{NS}TasaImp"),
                tax.findtext(f"{NS}MontoImp"),
            )
            for tax in node.findall(f"{NS}ImptoReten")
        ]
        return result

    @staticmethod
    def discounts(xml_bytes):
        return [
            (
                node.findtext(f"{NS}ValorDR"),
                node.findtext(f"{NS}IndExeDR"),
            )
            for node in etree.fromstring(xml_bytes).iter(f"{NS}DscRcgGlobal")
        ]

    def available(self, target):
        return NoteReferenceService(self.env).calculate_available_amounts(target)

    # Tests ---------------------------------------------------------------

    def test_original_capacity_is_net_of_discounts_with_additional_taxes(self):
        _source, target, _snapshot = self.mixed_source()
        available = self.available(target)
        self.assertEqual(
            (
                available["net"],
                available["exempt"],
                available["vat"],
                available["additional"],
                available["total"],
            ),
            (18000, 2700, 3420, 2050, 26170),
        )
        self.assertEqual(available["additional_taxes"], {"26": 2050})
        self.assertEqual(
            {
                ordinal: (
                    line["gross"],
                    line["discount"],
                    line["net"] + line["exempt"],
                    line["vat"],
                    line["additional"],
                    line["total"],
                )
                for ordinal, line in available["lines"].items()
            },
            {
                1: (10000, 1000, 9000, 1710, 2050, 12760),
                2: (10000, 1000, 9000, 1710, 0, 10710),
                3: (3000, 300, 2700, 0, 0, 2700),
            },
        )

    def test_full_cancellation_reproduces_additional_taxes_and_discounts(self):
        source, target, source_snapshot = self.mixed_source()
        note = self.create_credit_note(source, target, operation="cancel")
        note.invoice_user_id = self.dte_manager
        note.action_post()
        self.prepare_issuance(folio=810)
        document, signed = self.issue(note)
        assert_golden_xml(
            self, signed, GOLDEN_DIR / "dte_61_cancel_33_additional_tax_discount.xml"
        )
        self.assertTrue(NoteXmlComposer().validate_document(signed, strict_sii=True))
        totals = self.totals(signed)
        self.assertEqual(
            (
                totals["MntNeto"],
                totals["MntExe"],
                totals["IVA"],
                totals["ImptoReten"],
                totals["MntTotal"],
            ),
            ("18000", "2700", "3420", [("26", "20.50", "2050")], "26170"),
        )
        self.assertEqual(self.discounts(signed), [("2000", None), ("300", "1")])
        codes = [
            detail.findtext(f"{NS}CodImpAdic")
            for detail in etree.fromstring(signed).iter(f"{NS}Detalle")
        ]
        self.assertEqual(codes, ["26", None, None])
        snapshot = NoteReferenceService(self.env).read_original_snapshot(document)
        self.assertEqual(
            snapshot["global_discounts"], source_snapshot["global_discounts"]
        )
        available = self.available(target)
        self.assertEqual(available["total"], 0)
        self.assertEqual(available["additional_taxes"], {"26": 0})
        self.assertFalse(
            any(
                value
                for line in available["lines"].values()
                for key, value in line.items()
                if key != "quantity"
            )
        )

    def test_partial_credit_with_additional_tax(self):
        source, target, _snapshot = self.ila_source()
        note = self.partial_credit(source, target, {1: 4})
        self.assertEqual(note.amount_total, 4000 + 760 + 820)
        self.prepare_issuance(folio=820)
        _document, signed = self.issue(note)
        self.assertTrue(NoteXmlComposer().validate_document(signed, strict_sii=True))
        totals = self.totals(signed)
        self.assertEqual(
            (
                totals["MntNeto"],
                totals["IVA"],
                totals["ImptoReten"],
                totals["MntTotal"],
            ),
            ("4000", "760", [("26", "20.50", "820")], "5580"),
        )
        detail = etree.fromstring(signed).find(f".//{NS}Detalle")
        self.assertEqual(detail.findtext(f"{NS}CodImpAdic"), "26")
        self.assertEqual(self.discounts(signed), [])
        available = self.available(target)
        self.assertEqual(available["additional_taxes"], {"26": 1230})
        self.assertEqual(available["lines"][1]["additional"], 1230)
        self.assertEqual(available["total"], 20000 + 3800 + 2050 - 5580)

    def test_partial_credit_allocates_the_global_discount(self):
        source, target, _snapshot = self.mixed_source()
        self.prepare_issuance(folio=830)
        wrong = self.partial_credit(source, target, {1: 4}, discounts=[500])
        with (
            self.assertRaisesRegex(ValidationError, "must total 400 CLP"),
            self.env.cr.savepoint(),
        ):
            self.issue(wrong)
        note = self.partial_credit(source, target, {1: 4}, discounts=[400])
        _document, signed = self.issue(note)
        self.assertTrue(NoteXmlComposer().validate_document(signed, strict_sii=True))
        totals = self.totals(signed)
        self.assertEqual(
            (
                totals["MntNeto"],
                totals["IVA"],
                totals["ImptoReten"],
                totals["MntTotal"],
            ),
            ("3600", "684", [("26", "20.50", "820")], "5104"),
        )
        self.assertEqual(self.discounts(signed), [("400", None)])
        line = self.available(target)["lines"][1]
        self.assertEqual(
            (line["gross"], line["discount"], line["net"], line["additional"]),
            (6000, 600, 5400, 1230),
        )
        # The last credit of the line takes the whole remaining share.
        rest = self.partial_credit(source, target, {1: 6}, discounts=[600])
        self.issue(rest)
        line = self.available(target)["lines"][1]
        self.assertFalse(any(value for key, value in line.items() if key != "quantity"))

    def test_partial_credits_cannot_exceed_the_original(self):
        source, target, _snapshot = self.mixed_source()
        self.prepare_issuance(folio=840)
        first = self.partial_credit(source, target, {2: 1.5}, discounts=[750])
        self.issue(first)
        second = self.partial_credit(source, target, {2: 1})
        with (
            self.assertRaisesRegex(ValidationError, "available amount"),
            self.env.cr.savepoint(),
        ):
            self.issue(second)
        line = self.available(target)["lines"][2]
        self.assertEqual((line["gross"], line["discount"]), (2500, 250))

    def test_text_correction_stays_without_amounts(self):
        source, target, _snapshot = self.mixed_source()
        note = self.create_credit_note(source, target, operation="cancel")
        note.dte_note_operation = "text"
        note.action_post()
        self.prepare_issuance(folio=850)
        with (
            self.assertRaisesRegex(ValidationError, "must not change fiscal"),
            self.env.cr.savepoint(),
        ):
            self.issue(note)

    def debit_note(self, source, target, taxes):
        note = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.receiver.id,
                "invoice_date": fields.Date.from_string("2026-01-15"),
                "invoice_date_due": fields.Date.from_string("2026-01-15"),
                "invoice_user_id": self.dte_manager.id,
                "debit_origin_id": source.id,
                "l10n_latam_document_type_id": self.document_types["56"].id,
                **source._prepare_dte_note_values(
                    target, "amount", "Aumento de precio"
                ),
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Cerveza artesanal",
                            "quantity": 10,
                            "price_unit": 500,
                            "tax_ids": [Command.set(taxes.ids)],
                            "dte_note_origin_line_number": 1,
                        }
                    )
                ],
            }
        )
        note.action_post()
        return note

    def test_debit_note_applies_the_original_additional_tax(self):
        source, target, _snapshot = self.ila_source()
        self.prepare_issuance(code="56", folio=860)
        missing = self.debit_note(source, target, self.tax_19)
        with (
            self.assertRaisesRegex(ValidationError, "same additional SII tax"),
            self.env.cr.savepoint(),
        ):
            self.issue(missing)
        note = self.debit_note(source, target, self.tax_19 | self.ila)
        _document, signed = self.issue(note)
        self.assertTrue(NoteXmlComposer().validate_document(signed, strict_sii=True))
        totals = self.totals(signed)
        self.assertEqual(
            (
                totals["MntNeto"],
                totals["IVA"],
                totals["ImptoReten"],
                totals["MntTotal"],
            ),
            ("5000", "950", [("26", "20.50", "1025")], "6975"),
        )
        detail = etree.fromstring(signed).find(f".//{NS}Detalle")
        self.assertEqual(detail.findtext(f"{NS}CodImpAdic"), "26")
        # A debit increase does not consume credit capacity.
        self.assertEqual(self.available(target)["additional_taxes"], {"26": 2050})

    def accept(self, document):
        for state, role in (
            ("submitted", "sii_response"),
            ("accepted", "status_response"),
        ):
            document.action_record_transition(
                state,
                f"synthetic_{state}",
                artifact_id=self.create_artifact(document, role, b"synthetic").id,
            )

    def test_debit_cancellation_restores_a_credit_with_taxes_and_discounts(self):
        source, target, _snapshot = self.mixed_source()
        self.prepare_issuance(folio=870)
        credit = self.partial_credit(source, target, {1: 4}, discounts=[400])
        credit_document, credit_xml = self.issue(credit)
        self.accept(credit_document)
        wizard = (
            self.env["account.debit.note"]
            .with_user(self.dte_manager)
            .with_context(active_model="account.move", active_ids=credit.ids)
            .create(
                {
                    "move_ids": [Command.set(credit.ids)],
                    "reason": "Anula nota de credito",
                    "dte_note_operation": "cancel",
                }
            )
        )
        debit = self.env["account.move"].browse(wizard.create_debit()["res_id"])
        debit.invoice_user_id = self.dte_manager
        debit.action_post()
        self.prepare_issuance(code="56", folio=880, signing=False)
        debit_document, debit_xml = self.issue(debit)
        self.assertTrue(NoteXmlComposer().validate_document(debit_xml, strict_sii=True))
        self.assertEqual(self.totals(debit_xml), self.totals(credit_xml))
        self.assertEqual(self.discounts(debit_xml), [("400", None)])
        self.accept(debit_document)
        available = self.available(target)
        self.assertEqual(available["total"], 26170)
        self.assertEqual(available["lines"][1]["discount"], 1000)
