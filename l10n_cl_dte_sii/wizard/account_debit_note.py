# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, models
from odoo.exceptions import UserError


class AccountDebitNote(models.TransientModel):
    _inherit = "account.debit.note"

    def create_debit(self):
        self.ensure_one()
        rejected_dte = self.move_ids.filtered(
            lambda move: (
                move.company_id.account_fiscal_country_id.code == "CL"
                and move.l10n_cl_dte_status == "rejected"
            )
        )
        if rejected_dte:
            raise UserError(
                _(
                    "El DTE fue rechazado por el SII. Corrija y vuelva a emitir "
                    "el documento original en lugar de crear una nota de débito."
                )
            )
        return super().create_debit()
