# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models


class ElectronicDocument(models.Model):
    _inherit = "l10n_cl.electronic.document"

    def _lock_fiscal_chain(self):
        """Note extensions must lock their root chain before this default lock."""
        self.flush_recordset()
        self.env.cr.execute(
            "SELECT id FROM l10n_cl_electronic_document "
            "WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
            [self.ids],
        )
        self.invalidate_recordset()
