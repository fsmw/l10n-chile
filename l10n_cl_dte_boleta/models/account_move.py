# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models

from ..services.boleta_issuance_adapter import BoletaIssuanceAdapter


class AccountMove(models.Model):
    _inherit = "account.move"

    dte_boleta_payment_method = fields.Selection(
        [
            ("1", "Cash"),
            ("2", "Electronic payment"),
            ("3", "Electronic transfer"),
            ("4", "Cheque"),
            ("5", "Other"),
        ],
        string="Boleta Payment Method",
        help=(
            "Required for boletas whose total exceeds the annual 135 UF limit. "
            "For cash, the receiver email and phone are also required."
        ),
        copy=False,
    )

    def _get_dte_issuance_adapter(self, document_code):
        """Select a dedicated format adapter for Chilean electronic boletas."""
        self.ensure_one()
        if str(document_code) in BoletaIssuanceAdapter.SUPPORTED_DOCUMENT_CODES:
            return BoletaIssuanceAdapter()
        return super()._get_dte_issuance_adapter(document_code)
