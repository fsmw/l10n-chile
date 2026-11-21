# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models

from ..services.common import PURCHASE_CODE, SALE_MOVE_TYPE


class IncomingDteAccountBinding(models.Model):
    _inherit = "l10n_cl.incoming.dte.account.binding"

    document_code = fields.Selection(
        selection_add=[(PURCHASE_CODE, "Electronic Purchase Invoice")],
        ondelete={PURCHASE_CODE: "cascade"},
    )

    def _expected_move_type(self):
        # The buyer issued the DTE 46; for us it documents a customer invoice.
        if self.document_code == PURCHASE_CODE:
            return SALE_MOVE_TYPE
        return super()._expected_move_type()
