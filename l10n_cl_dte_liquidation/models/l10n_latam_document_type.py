# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models


class L10nLatamDocumentType(models.Model):
    _inherit = "l10n_latam.document.type"

    def _is_doc_type_vendor(self):
        """The company numbers the 43 it issues on a vendor bill, as the 46."""
        return super()._is_doc_type_vendor() or (
            self.code == "43" and self.country_id.code == "CL"
        )
