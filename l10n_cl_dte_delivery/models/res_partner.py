# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    l10n_cl_dte_email = fields.Char(
        string="DTE Exchange Email",
        help="Mailbox the receiver registered with SII to receive electronic "
        "tax documents. The partner email is used when it is empty.",
    )

    def _commercial_fields(self):
        return super()._commercial_fields() + ["l10n_cl_dte_email"]
