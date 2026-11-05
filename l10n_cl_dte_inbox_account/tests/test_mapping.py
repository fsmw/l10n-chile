# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from decimal import Decimal

from psycopg2 import IntegrityError

from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tools import mute_logger

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
        doc = self.document(self.source())
        self.mapping()
        doc["totals"]["total"] = str(Decimal(doc["totals"]["total"]) + 1)
        mapped = IncomingAccountMappingService(self.op_env).resolve(
            doc, self.supplier, {1: {"uom_id": self.unit.id}}
        )
        self.assert_code(
            "amount_mismatch",
            IncomingAccountComparisonService(self.op_env).preview,
            doc,
            mapped,
            self.purchase_journal,
        )

    def test_quantity_discount_consistency(self):
        doc = self.document(self.source())
        self.mapping()
        doc["lines"][0]["discount_percent"] = "10"
        doc["lines"][0]["discount_amount"] = "1"
        mapped = IncomingAccountMappingService(self.op_env).resolve(
            doc, self.supplier, {1: {"uom_id": self.unit.id}}
        )
        self.assert_code(
            "amount_mismatch",
            IncomingAccountComparisonService(self.op_env).preview,
            doc,
            mapped,
            self.purchase_journal,
        )
