# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models


class ElectronicSubmission(models.Model):
    _inherit = "l10n_cl.electronic.submission"

    def _job_query_sii_status(self):
        """Queue the customer delivery once SII accepts a Production DTE.

        Demo and Certification documents have no fiscal value and are never
        sent to the customer automatically.
        """
        result = super()._job_query_sii_status()
        self._queue_customer_delivery()
        return result

    def _queue_customer_delivery(self):
        document = self.document_id.sudo()
        move = document.source_move_id
        if (
            self.state == "accepted"
            and document.sii_environment == "production"
            and move
            and move.dte_delivery_state == "not_sent"
        ):
            move.with_delay(identity_key=f"dte-deliver-{document.id}")._job_deliver_dte(
                document.id
            )
