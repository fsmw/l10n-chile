# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import PosReturnCase


@tagged("post_install", "-at_install", "dte_pos_return")
class TestPosReturnTracking(PosReturnCase):
    def tracked_sale(self, tracking):
        self.product.write({"is_storable": True, "tracking": tracking})
        self.company.dte_pos_accounting_mode = "session_summary"
        self.config.picking_type_id.write(
            {"use_create_lots": True, "use_existing_lots": True}
        )
        names = (
            ["ORIGINAL-A", "ORIGINAL-B"] if tracking == "serial" else ["ORIGINAL-LOT"]
        )
        lots = self.env["stock.lot"]
        for name in names:
            lot = self.env["stock.lot"].create(
                {
                    "name": name,
                    "product_id": self.product.id,
                    "company_id": self.company.id,
                }
            )
            lots |= lot
            self.env["stock.quant"]._update_available_quantity(
                self.product,
                self.config.picking_type_id.default_location_src_id,
                1 if tracking == "serial" else 2,
                lot_id=lot,
            )
        self.open_new_session()
        data = self.create_ui_order_data([(self.product, 2)], customer=self.customer)
        data["lines"][0][2]["pack_lot_ids"] = [
            Command.create({"lot_name": name}) for name in names
        ]
        result = self.env["pos.order"].sync_from_ui([data])
        return self.env["pos.order"].browse(result["pos.order"][0]["id"]), lots

    def return_lot(self, original, name):
        data = self.refund_data(original)
        data["lines"][0][2]["pack_lot_ids"] = [Command.create({"lot_name": name})]
        return self.refund(original, data=data)

    def test_native_single_lot_return_preserves_stock_identity(self):
        original, lot = self.tracked_sale("lot")
        order = self.return_lot(original, lot.name)
        self.assertEqual(order.picking_ids.move_line_ids.lot_id, lot)
        self.assertEqual(
            order.picking_ids.move_line_ids.location_dest_id,
            self.config.picking_type_id.default_location_src_id,
        )

    def test_wrong_lot_is_blocked_before_stock_return(self):
        original, _lots = self.tracked_sale("lot")
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.return_lot(original, "OTHER-LOT")

    def test_original_lot_record_cannot_be_rewritten(self):
        original, _lots = self.tracked_sale("lot")
        with self.assertRaises(UserError), self.cr.savepoint():
            original.lines.pack_lot_ids.write({"lot_name": "OTHER-LOT"})

    def test_serial_cannot_be_returned_twice(self):
        original, lots = self.tracked_sale("serial")
        first = self.return_lot(original, lots[0].name)
        self.assertEqual(first.picking_ids.move_line_ids.lot_id, lots[0])
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.return_lot(original, lots[0].name)
        second = self.return_lot(original, lots[1].name)
        self.assertEqual(second.picking_ids.move_line_ids.lot_id, lots[1])
