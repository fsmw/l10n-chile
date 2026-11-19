# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models


class ElectronicSubmission(models.Model):
    _inherit = "l10n_cl.electronic.submission"

    def _job_query_sii_status(self):
        """Queue the customer delivery once SII accepts an invoice DTE."""
        result = super()._job_query_sii_status()
        move = self.document_id.sudo().source_move_id
        if self.state == "accepted" and move and move.dte_delivery_state == "not_sent":
            move.with_delay(
                identity_key=f"dte-deliver-{self.document_id.id}"
            )._job_deliver_dte()
        return result
