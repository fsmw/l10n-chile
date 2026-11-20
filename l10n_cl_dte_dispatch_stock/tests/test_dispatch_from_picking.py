# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDispatchFromPicking(TransactionCase):
    """A validated delivery turns into a draft DTE 52 guide."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.groups_id |= cls.env.ref("l10n_cl_dte_core.group_dte_user")
        cls.customer = cls.env["res.partner"].create(
            {"name": "Cliente GD", "street": "Destino 456", "city": "Santiago"}
        )
        cls.product = cls.env["product.product"].create(
            {"name": "Caja de repuestos", "type": "consu", "lst_price": 2500}
        )
        cls.warehouse = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.env.company.id)], limit=1
        )

    def _delivery(self, quantity=3):
        picking = self.env["stock.picking"].create(
            {
                "partner_id": self.customer.id,
                "picking_type_id": self.warehouse.out_type_id.id,
                "location_id": self.warehouse.lot_stock_id.id,
                "location_dest_id": self.env.ref("stock.stock_location_customers").id,
                "move_ids": [
                    (
                        0,
                        0,
                        {
                            "name": self.product.name,
                            "product_id": self.product.id,
                            "product_uom_qty": quantity,
                            "product_uom": self.product.uom_id.id,
                            "location_id": self.warehouse.lot_stock_id.id,
                            "location_dest_id": self.env.ref(
                                "stock.stock_location_customers"
                            ).id,
                        },
                    )
                ],
            }
        )
        picking.action_confirm()
        picking.move_ids.quantity = quantity
        picking.move_ids.picked = True
        picking.button_validate()
        self.assertEqual(picking.state, "done")
        return picking

    def test_validated_delivery_creates_a_valued_guide(self):
        picking = self._delivery()
        action = picking.action_create_dte_dispatch()
        dispatch = self.env["l10n_cl.dte.dispatch"].browse(action["res_id"])
        self.assertEqual(dispatch.picking_id, picking)
        self.assertEqual(dispatch.source_kind, "stock_picking")
        self.assertEqual(dispatch.partner_id, self.customer)
        self.assertEqual(dispatch.destination_address, "Destino 456")
        self.assertEqual(dispatch.state, "draft")
        self.assertEqual(dispatch.line_ids.product_id, self.product)
        self.assertEqual(dispatch.line_ids.quantity, 3)
        self.assertEqual(dispatch.line_ids.unit_price, 2500)
        with self.assertRaises(UserError):
            picking.action_create_dte_dispatch()

    def test_unvalued_policy_and_pending_delivery(self):
        self.env.company.dte_dispatch_valued = False
        dispatch = self.env["l10n_cl.dte.dispatch"].browse(
            self._delivery().action_create_dte_dispatch()["res_id"]
        )
        self.assertEqual(dispatch.line_ids.tax_category, "unvalued")
        self.assertFalse(dispatch.line_ids.unit_price)
        pending = self.env["stock.picking"].create(
            {
                "partner_id": self.customer.id,
                "picking_type_id": self.warehouse.out_type_id.id,
            }
        )
        with self.assertRaises(UserError):
            pending.action_create_dte_dispatch()
