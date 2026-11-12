# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, models


class DteTypedSourceRegistry(models.AbstractModel):
    _inherit = "l10n_cl.dte.source.registry"

    @api.model
    def _dte_source_relations(self):
        relations = super()._dte_source_relations()
        relations["account.move"] = "source_move_id"
        return relations
