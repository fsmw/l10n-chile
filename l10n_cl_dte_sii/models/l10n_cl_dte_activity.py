# Copyright 2026
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class L10nClDteActivity(models.Model):
    _name = "l10n_cl.dte.activity"
    _description = "Actividad económica SII"
    _order = "code"
    _rec_names_search = ["code", "name"]

    code = fields.Char(string="Código", size=6, required=True, index=True)
    name = fields.Char(string="Glosa", required=True, translate=True)
    vat_affected = fields.Char(string="Afecto a IVA", size=3)
    tax_category = fields.Char(string="Categoría tributaria", size=3)
    internet_available = fields.Boolean(string="Disponible en Internet", default=True)
    active = fields.Boolean(
        default=True,
        help="Permite ocultar la actividad sin eliminarla del catálogo.",
    )

    _sql_constraints = [
        (
            "code_uniq",
            "unique(code)",
            "El código de actividad económica debe ser único.",
        ),
    ]

    @api.depends("code", "name")
    def _compute_display_name(self):
        for activity in self:
            activity.display_name = f"({activity.code}) {activity.name}"
