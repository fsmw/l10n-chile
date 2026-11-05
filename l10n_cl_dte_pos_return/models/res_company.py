# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import UserError


class ResCompany(models.Model):
    _inherit = "res.company"

    dte_pos_returns_enabled = fields.Boolean(
        string="Enable Chilean POS Returns",
        default=False,
        copy=False,
        groups="l10n_cl_dte_core.group_dte_manager",
        help="Allow referenced online POS goods returns within this company.",
    )

    def write(self, vals):
        if "dte_pos_returns_enabled" in vals:
            for company in self:
                if company.sudo().dte_pos_returns_enabled != vals[
                    "dte_pos_returns_enabled"
                ] and self.env["pos.session"].sudo().search_count(
                    [
                        ("company_id", "=", company.id),
                        (
                            "state",
                            "in",
                            ["opening_control", "opened", "closing_control"],
                        ),
                    ]
                ):
                    raise UserError(
                        _(
                            "Close all active POS sessions before changing the "
                            "return policy."
                        )
                    )
        return super().write(vals)
