# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class AccountPaymentTerm(models.Model):
    _inherit = "account.payment.term"

    l10n_cl_sii_code = fields.Selection(
        [("1", "Cash payment"), ("2", "Credit"), ("3", "Without payment")],
        string="SII payment form",
        default="2",
        help="Payment form reported in Chilean electronic invoices.",
    )
