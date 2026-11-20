# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.exceptions import ValidationError

MANAGER_GROUP = "l10n_cl_dte_core.group_dte_manager"


class ResCompany(models.Model):
    _inherit = "res.company"

    dte_rcv_daily_read = fields.Boolean(
        string="Daily SII RCV Read",
        help="Read the purchase and sales register of the current and previous "
        "month from the SII every day, with the production certificate.",
    )
    dte_rcv_read_user_id = fields.Many2one(
        "res.users",
        string="RCV Read User",
        help="DTE manager recorded as the requester of the daily reads. "
        "The user must have access to this company.",
    )

    @api.constrains("dte_rcv_daily_read", "dte_rcv_read_user_id")
    def _check_dte_rcv_read_user(self):
        for company in self:
            if company.dte_rcv_daily_read and not company._dte_rcv_read_user_is_valid():
                raise ValidationError(
                    self.env._(
                        "Select an active DTE manager with access to the company "
                        "before enabling the daily RCV read."
                    )
                )

    def _dte_rcv_read_user_is_valid(self):
        self.ensure_one()
        user = self.dte_rcv_read_user_id
        return bool(
            user
            and user.active
            and self in user.company_ids
            and user.has_group(MANAGER_GROUP)
        )
