# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import UserError


class ResCompany(models.Model):
    _inherit = "res.company"

    dte_pos_accounting_mode = fields.Selection(
        selection=[
            ("per_order_invoice", "Invoice per POS order (standard accounting)"),
            ("session_summary", "DTE per order, accounting at session close"),
        ],
        string="POS DTE Accounting Mode",
        default="per_order_invoice",
        required=True,
        index=True,
        copy=False,
        groups="l10n_cl_dte_core.group_dte_manager",
    )

    def write(self, vals):
        """Protect the company policy while a POS session is active."""
        if "dte_pos_accounting_mode" in vals:
            for company in self:
                elevated_company = company.sudo()
                if (
                    vals["dte_pos_accounting_mode"]
                    == elevated_company.dte_pos_accounting_mode
                ):
                    continue
                active = (
                    self.env["pos.session"]
                    .sudo()
                    .search_count(
                        [
                            ("company_id", "=", company.id),
                            (
                                "state",
                                "in",
                                ["opening_control", "opened", "closing_control"],
                            ),
                        ]
                    )
                )
                if active:
                    raise UserError(
                        _(
                            "Close all active POS sessions before changing the "
                            "company DTE accounting mode."
                        )
                    )
        return super().write(vals)
