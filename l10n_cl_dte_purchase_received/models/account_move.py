# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models
from odoo.exceptions import UserError

from ..services.common import PURCHASE_CODE, SALE_MOVE_TYPE
from ..services.received_service import ReceivedPurchaseService


class AccountMove(models.Model):
    _inherit = "account.move"

    def _is_received46(self):
        """A customer invoice documenting a DTE 46 that our buyer issued."""
        self.ensure_one()
        return (
            self.move_type == SALE_MOVE_TYPE
            and self.l10n_latam_document_type_id.code == PURCHASE_CODE
        )

    def _is_manual_document_number(self):
        # The folio belongs to the buyer's CAF, never to our sequence.
        if self._is_received46():
            return True
        return super()._is_manual_document_number()

    def _compute_dte_issuance_available(self):
        received = self.filtered(lambda move: move._is_received46())
        received.dte_issuance_available = False
        return super(AccountMove, self - received)._compute_dte_issuance_available()

    def issue_dte(self):
        self.ensure_one()
        if self._is_received46():
            raise UserError(
                self.env._(
                    "The buyer already issued this purchase invoice (DTE 46);"
                    " it cannot be issued again as our DTE."
                )
            )
        return super().issue_dte()

    def _check_incoming_account_evidence(self):
        received = self.filtered(lambda move: move._is_received46())
        service = ReceivedPurchaseService(self.env)
        for move in received:
            service.check_posting(move)
        return super(AccountMove, self - received)._check_incoming_account_evidence()
