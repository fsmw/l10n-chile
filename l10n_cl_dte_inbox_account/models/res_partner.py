# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models
from odoo.exceptions import AccessError, UserError

from ..services.binding_context import trusted
from ..services.binding_service import IncomingAccountBindingService
from ..services.common import BINDING, vat


class ResPartner(models.Model):
    _inherit = "res.partner"

    dte_inbox_account_lock_revision = fields.Integer(
        string="Incoming DTE Lock Revision",
        default=0,
        readonly=True,
        copy=False,
        groups="base.group_system",
        help=(
            "Technical serialization revision; never changes the supplier "
            "fiscal identity."
        ),
    )

    def write(self, vals):
        if "dte_inbox_account_lock_revision" in vals and not trusted(self.env):
            raise AccessError(
                self.env._("The supplier serialization revision is server-owned.")
            )
        if any(
            ("vat" in vals and vat(record.vat) != vat(vals["vat"]))
            or ("parent_id" in vals and record.parent_id.id != vals["parent_id"])
            or ("company_id" in vals and record.company_id.id != vals["company_id"])
            for record in self
        ):
            roots = self.commercial_partner_id
            bindings = (
                self.env[BINDING].sudo().search_count([("partner_id", "in", roots.ids)])
            )
            if bindings:
                raise UserError(
                    self.env._(
                        "A supplier with incoming DTE provenance cannot change its "
                        "fiscal identity or commercial root."
                    )
                )
            enabled = (
                self.env["res.company"]
                .sudo()
                .search_count([("dte_inbox_account_enabled", "=", True)])
            )
            if enabled:
                IncomingAccountBindingService(self.env)._lock_suppliers(roots)
        return super().write(vals)
