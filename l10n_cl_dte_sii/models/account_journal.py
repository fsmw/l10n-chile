# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class AccountJournal(models.Model):
    _inherit = "account.journal"

    l10n_cl_dte_point_of_sale = fields.Selection(
        [("online", "Online"), ("manual", "Manual")],
        string="Modo de emisión DTE",
        help="Manual journals do not generate or send DTE through this addon.",
    )
    l10n_cl_dte_sucursal = fields.Char(string="Código de sucursal SII")
    l10n_cl_dte_office_number = fields.Char(string="Número de oficina SII")
