# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models


class DteCertificationImportWizard(models.TransientModel):
    _name = "l10n_cl.dte.certification.import.wizard"
    _description = "Import Original SII Certification Set"

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    source_file = fields.Binary(required=True, attachment=False)
    source_filename = fields.Char(required=True)

    def action_import(self):
        self.ensure_one()
        certification_set = self.env[
            "l10n_cl.dte.certification.set"
        ]._create_from_import(self.company_id, self.source_filename, self.source_file)
        return {
            "type": "ir.actions.act_window",
            "name": _("Certification Set Source"),
            "res_model": "l10n_cl.dte.certification.set",
            "res_id": certification_set.id,
            "view_mode": "form",
            "target": "current",
        }
