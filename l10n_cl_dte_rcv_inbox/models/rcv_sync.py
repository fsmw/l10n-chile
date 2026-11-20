# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models


class RcvSync(models.Model):
    _inherit = "l10n_cl.dte.rcv.sync"

    def _on_rcv_documents_stored(self):
        """Link the stored purchases to their received DTEs, then reconcile."""
        self.ensure_one()
        self.env["l10n_cl.dte.rcv.document"].search(
            [("last_sync_id", "=", self.id), ("operation", "=", "purchase")]
        )._link_rcv_incoming_dte()
        return super()._on_rcv_documents_stored()
