# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services.binding_context import trusted
from ..services.binding_service import IncomingAccountBindingService
from ..services.common import BINDING


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    dte_inbox_line_number = fields.Integer(
        string="Incoming DTE Line", readonly=True, copy=False, default=0
    )

    @api.model_create_multi
    def create(self, vals_list):
        self._lock_incoming_suppliers(
            self.env["account.move"].browse(
                [vals["move_id"] for vals in vals_list if vals.get("move_id")]
            )
        )
        if not trusted(self.env):
            for vals in vals_list:
                if vals.get("dte_inbox_line_number"):
                    raise AccessError(
                        self.env._("Incoming DTE line ordinals are server-owned.")
                    )
                if vals.get("display_type", "product") == "product" and self.env[
                    BINDING
                ].sudo().search_count([("move_id", "=", vals.get("move_id"))]):
                    raise UserError(
                        self.env._("A bound bill must keep its signed fiscal details.")
                    )
        return super().create(vals_list)

    def write(self, vals):
        self._lock_incoming_suppliers(
            self.move_id | self.env["account.move"].browse(vals.get("move_id"))
        )
        if not trusted(self.env):
            if "dte_inbox_line_number" in vals:
                raise AccessError(
                    self.env._("Incoming DTE line ordinals are server-owned.")
                )
            changed_bound = self.filtered(
                lambda line: (
                    line.dte_inbox_line_number
                    and (
                        ("move_id" in vals and vals["move_id"] != line.move_id.id)
                        or (
                            "display_type" in vals
                            and vals["display_type"] != line.display_type
                        )
                    )
                )
            )
            target_bound = (
                "move_id" in vals
                and any(line.move_id.id != vals["move_id"] for line in self)
                and self.env[BINDING]
                .sudo()
                .search_count([("move_id", "=", vals["move_id"])])
            )
            new_product = vals.get("display_type") == "product" and any(
                line.display_type != "product"
                and self.env[BINDING]
                .sudo()
                .search_count([("move_id", "=", line.move_id.id)])
                for line in self
            )
            if changed_bound or target_bound or new_product:
                raise UserError(
                    self.env._("A bound fiscal line cannot move or become a section.")
                )
        return super().write(vals)

    def unlink(self):
        self._lock_incoming_suppliers(self.move_id)
        if self.filtered("dte_inbox_line_number") and not trusted(self.env):
            raise UserError(
                self.env._(
                    "The signed fiscal details of a bound bill cannot be deleted."
                )
            )
        return super().unlink()

    def _lock_incoming_suppliers(self, moves):
        if trusted(self.env):
            return
        for move in moves.exists():
            if (
                move.move_type == "in_invoice"
                and move.l10n_latam_document_type_id.code in {"33", "34"}
                and (
                    move.company_id.dte_inbox_account_enabled
                    or self.env[BINDING]
                    .sudo()
                    .search_count([("move_id", "=", move.id)])
                )
            ):
                move.check_access("write")
                IncomingAccountBindingService(self.env)._lock_suppliers(move.partner_id)
