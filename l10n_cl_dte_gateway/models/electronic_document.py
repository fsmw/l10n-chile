# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, models
from odoo.exceptions import UserError


class ElectronicDocument(models.Model):
    _inherit = "l10n_cl.electronic.document"

    def _check_signature_regeneration(self):
        result = super()._check_signature_regeneration()
        if (
            self.env["l10n_cl.electronic.submission"]
            .sudo()
            .search_count([("document_id", "=", self.id)])
        ):
            raise UserError(_("DTE with an SII submission cannot be regenerated."))
        return result
