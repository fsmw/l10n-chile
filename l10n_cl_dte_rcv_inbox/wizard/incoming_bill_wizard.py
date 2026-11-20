# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models

from odoo.addons.l10n_cl_dte_rcv.services.rcv_matcher import RcvMatcher

from ..services.rcv_draft_service import MANAGER_GROUP


class IncomingDteBillWizard(models.TransientModel):
    _inherit = "l10n_cl.incoming.dte.bill.wizard"

    def action_confirm(self):
        action = super().action_confirm()
        self._reconcile_rcv_documents()
        return action

    def _reconcile_rcv_documents(self):
        """Reconcile the RCV purchases of the DTE with its new vendor bill.

        Only a DTE manager reconciles; otherwise the next reconciliation of
        the RCV links them.
        """
        self.ensure_one()
        incoming = self.incoming_document_id
        incoming._link_rcv_documents()
        matcher = RcvMatcher(self.env)
        if not self.env.user.has_group(MANAGER_GROUP) or not matcher.can_read_moves():
            return False
        documents = self.env["l10n_cl.dte.rcv.document"].search(
            [("incoming_dte_id", "=", incoming.id)]
        )
        matcher.reconcile(documents)
        return True
