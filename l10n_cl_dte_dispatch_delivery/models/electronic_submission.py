# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models


class ElectronicSubmission(models.Model):
    _inherit = "l10n_cl.electronic.submission"

    def _queue_customer_delivery(self):
        """Also deliver accepted Production guides, never Demo or Certification."""
        result = super()._queue_customer_delivery()
        document = self.document_id.sudo()
        dispatch = document.source_dispatch_id
        if (
            self.state == "accepted"
            and document.sii_environment == "production"
            and dispatch
            and dispatch.dte_delivery_state == "not_sent"
        ):
            dispatch.with_delay(
                identity_key=f"dte-deliver-{document.id}"
            )._job_deliver_dte(document.id)
        return result
