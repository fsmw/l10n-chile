# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    l10n_cl_dte_exchange_auto_send = fields.Boolean(
        string="Email Exchange Responses After SII Registry",
        help="After an ACD, RCD or ERM action is registered with SII, sign and "
        "email the matching RespuestaDTE or EnvioRecibos to the supplier. "
        "When disabled, the response is only offered.",
    )
    l10n_cl_dte_exchange_site = fields.Char(
        string="Goods Receipt Place",
        size=80,
        help="Default place (Recinto) declared in Law 19.983 receipts. The "
        "company address is used when it is empty.",
    )
