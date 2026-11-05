# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PosSession(models.Model):
    _inherit = "pos.session"

    dte_pos_returns_enabled = fields.Boolean(
        string="Chilean POS Returns Enabled",
        readonly=True,
        copy=False,
        default=False,
    )
    dte_return_order_count = fields.Integer(compute="_compute_dte_return_totals")
    dte_return_total = fields.Monetary(
        compute="_compute_dte_return_totals",
        currency_field="currency_id",
    )

    @api.depends(
        "order_ids.amount_total",
        "order_ids.dte_return_target_document_id",
        "order_ids.state",
    )
    def _compute_dte_return_totals(self):
        for session in self:
            orders = session.order_ids.filtered(
                lambda order: (
                    order.dte_return_target_document_id
                    and order.state in {"paid", "done", "invoiced"}
                )
            )
            session.dte_return_order_count = len(orders)
            session.dte_return_total = sum(orders.mapped("amount_total"))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            config = self.env["pos.config"].browse(vals.get("config_id"))
            vals["dte_pos_returns_enabled"] = (
                config.company_id.sudo().dte_pos_returns_enabled
            )
        return super().create(vals_list)

    def action_pos_session_open(self):
        for session in self.filtered(lambda item: item.state == "opening_control"):
            session.dte_pos_returns_enabled = (
                session.company_id.sudo().dte_pos_returns_enabled
            )
        return super().action_pos_session_open()

    def write(self, vals):
        if "dte_pos_returns_enabled" in vals:
            for session in self:
                expected = session.company_id.sudo().dte_pos_returns_enabled
                if vals["dte_pos_returns_enabled"] != expected or (
                    session.state != "opening_control"
                    and vals["dte_pos_returns_enabled"]
                    != session.dte_pos_returns_enabled
                ):
                    raise UserError(
                        _("The POS session return policy is frozen from its company.")
                    )
        return super().write(vals)

    def _get_pos_dte_orders_for_closing(self):
        orders = super()._get_pos_dte_orders_for_closing()
        return orders | self.order_ids.filtered(
            lambda order: (
                order.dte_return_target_document_id
                and order.state in {"paid", "done", "invoiced"}
            )
        )

    def _get_pos_dte_close_document(self, order):
        document = super()._get_pos_dte_close_document(order)
        if order.dte_return_target_document_id and (
            document.document_code != "61"
            or document.note_pos_return_order_id != order
            or document.fiscal_state in {"rejected", "outcome_unknown"}
        ):
            raise UserError(
                _(
                    "A rejected, unknown or inconsistent POS credit note "
                    "blocks session closing."
                )
            )
        return document

    def _get_pos_dte_signed_total(self, order, document):
        total = super()._get_pos_dte_signed_total(order, document)
        return -total if order.dte_return_target_document_id else total

    @api.model
    def _load_pos_data_fields(self, config_id):
        return super()._load_pos_data_fields(config_id) + ["dte_pos_returns_enabled"]
