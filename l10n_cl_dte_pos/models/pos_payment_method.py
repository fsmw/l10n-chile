# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    l10n_cl_dte_boleta_payment_method = fields.Selection(
        selection=[
            ("1", "Cash"),
            ("2", "Electronic payment"),
            ("3", "Electronic transfer"),
            ("4", "Cheque"),
            ("5", "Other"),
        ],
        string="SII Boleta Payment Method",
        help=(
            "Map this POS payment method to the SII code used for boletas over "
            "135 UF. Every payment method in such an order must map to the same code."
        ),
    )
