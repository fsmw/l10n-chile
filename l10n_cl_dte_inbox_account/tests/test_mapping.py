# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from decimal import Decimal

from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from ..services.common import BINDING
from ..services.comparison_service import IncomingAccountComparisonService
from ..services.mapping_service import IncomingAccountMappingService
from .common import IncomingAccountCase


@tagged("post_install", "-at_install")
class TestIncomingAccountMapping(IncomingAccountCase):
    def test_exact_code_precedence(self):
        source = self.source()
        doc = self.document(source)
        default = self.mapping()
        description = self.mapping(
            name="Description",
            match_kind="description",
            source_value=doc["lines"][0]["name"],
        )
        resolved = IncomingAccountMappingService(self.op_env).resolve(
            doc, self.supplier, {1: {"uom_id": self.unit.id}}
        )
        self.assertEqual(resolved[0]["mapping_id"], description.id)
        doc["lines"][0]["codes"] = [{"type": "SKU", "value": "A"}]
        exact = self.mapping(
            name="Code", match_kind="code", source_code_type="SKU", source_value="A"
        )
        self.assertEqual(
            IncomingAccountMappingService(self.op_env).resolve(
                doc, self.supplier, {1: {"uom_id": self.unit.id}}
            )[0]["mapping_id"],
            exact.id,
        )
        self.assertNotEqual(default, exact)

    def test_ambiguous_item_codes(self):
        doc = self.document(self.source())
        doc["lines"][0]["codes"] = [
            {"type": "SKU", "value": "A"},
            {"type": "SKU", "value": "B"},
        ]
        self.mapping(match_kind="code", source_code_type="SKU", source_value="A")
        self.mapping(match_kind="code", source_code_type="SKU", source_value="B")
        self.assert_code(
            "mapping_ambiguous",
            IncomingAccountMappingService(self.op_env).resolve,
            doc,
            self.supplier,
        )
        with (
            self.assertRaises(IntegrityError),
            self.cr.savepoint(),
            mute_logger("odoo.sql_db"),
        ):
            self.mapping(match_kind="code", source_code_type="SKU", source_value="A")

    def test_manual_override_revalidated(self):
        wizard = self.wizard()
        with self.assertRaises(UserError), self.cr.savepoint():
            wizard.line_ids[0].account_id = self.company_data["default_account_expense"]
        wrong = self.purchase_vat.sudo().copy({"amount": 18})
        wizard.line_ids[0].tax_id = wrong
        self.assert_code("tax_invalid", wizard.action_confirm)
        self.assertFalse(self.op_env[BINDING].search([]))

    def test_optional_product_and_unit(self):
        wizard = self.wizard()
        self.assertFalse(wizard.line_ids.product_id)
        wizard.line_ids[0].uom_id = False
        wizard.action_refresh_preview()
        if wizard.line_ids[0].source_unit:
            self.assertTrue(wizard.has_blockers)
            self.assertIn("uom_unresolved", wizard.diagnostic_text)
        wizard.line_ids[0].uom_id = self.unit
        wizard.action_refresh_preview()
        move = self.bill(wizard)
        self.assertFalse(move.invoice_line_ids.product_id)

    def test_purchase_tax_semantics(self):
        wizard = self.wizard()
        wrong = self.purchase_vat.sudo().copy(
            {"price_include_override": "tax_included"}
        )
        wizard.line_ids[0].tax_id = wrong
        self.assert_code("tax_invalid", wizard.action_confirm)

    def test_mapping_company_and_archive(self):
        mapping = self.mapping()
        mapping.action_archive()
        doc = self.document(self.source())
        self.assert_code(
            "mapping_missing",
            IncomingAccountMappingService(self.op_env).resolve,
            doc,
            self.supplier,
        )
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.mapping(
                match_kind="description",
                source_value="Foreign account",
                account_id=self.company_data["default_account_expense"].id,
            )

    def test_decimal_and_rounding(self):
        wizard = self.wizard()
        doc = self.document(wizard.incoming_document_id)
        doc["totals"]["total"] = str(Decimal(doc["totals"]["total"]) + 1)
        mapped = IncomingAccountMappingService(self.op_env).resolve(
            doc, self.supplier, wizard._prepare_overrides()
        )
        self.assert_code(
            "amount_mismatch",
            IncomingAccountComparisonService(self.op_env).preview,
            doc,
            mapped,
            self.purchase_journal,
        )

    def test_quantity_discount_consistency(self):
        wizard = self.wizard()
        doc = self.document(wizard.incoming_document_id)
        doc["lines"][0]["discount_percent"] = "10"
        doc["lines"][0]["discount_amount"] = "1"
        mapped = IncomingAccountMappingService(self.op_env).resolve(
            doc, self.supplier, wizard._prepare_overrides()
        )
        self.assert_code(
            "amount_mismatch",
            IncomingAccountComparisonService(self.op_env).preview,
            doc,
            mapped,
            self.purchase_journal,
        )
