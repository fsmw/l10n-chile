# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.l10n_cl_dte_inbox_account.services.common import GROUP

from ..services.rcv_draft_service import MANAGER_GROUP


class ResCompany(models.Model):
    _inherit = "res.company"

    dte_rcv_auto_draft = fields.Boolean(
        string="Automatic RCV Vendor Bill Drafts",
        help="Every day, prepare a vendor bill draft for each registered RCV "
        "purchase missing in accounting: from the signed XML of its received "
        "DTE when the inbox holds it, otherwise from the RCV amounts. Drafts "
        "are never posted and suppliers are never created.",
    )
    dte_rcv_draft_user_id = fields.Many2one(
        "res.users",
        string="RCV Draft User",
        help="DTE manager, with the incoming DTE vendor bill role, on whose "
        "behalf and with whose access rights the automatic drafts are created.",
    )

    @api.constrains("dte_rcv_auto_draft", "dte_rcv_draft_user_id")
    def _check_dte_rcv_draft_user(self):
        for company in self:
            if (
                company.dte_rcv_auto_draft
                and not company._dte_rcv_draft_user_is_valid()
            ):
                raise ValidationError(
                    self.env._(
                        "Select an active DTE manager with the incoming DTE vendor "
                        "bill role and access to the company before enabling the "
                        "automatic RCV drafts."
                    )
                )

    def _dte_rcv_draft_user_is_valid(self):
        self.ensure_one()
        user = self.dte_rcv_draft_user_id
        return bool(
            user
            and user.active
            and self in user.company_ids
            and user.has_group(MANAGER_GROUP)
            and user.has_group(GROUP)
        )
