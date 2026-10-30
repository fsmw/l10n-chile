# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo import _, fields, models
from odoo.exceptions import UserError


class DteCafUpload(models.TransientModel):
    _name = "l10n_cl.dte.caf.upload"
    _description = "Import SII CAF"

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    upload_filename = fields.Char(required=True)
    upload_data = fields.Binary(required=True, attachment=False)

    def action_import(self):
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_sii.group_dte_manager"):
            raise UserError(_("Only a DTE manager can import CAF files."))
        payload = base64.b64decode(self.upload_data)
        caf = self.env["l10n_cl.dte.caf"].create_from_xml(
            self.upload_filename, payload, self.company_id
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "l10n_cl.dte.caf",
            "res_id": caf.id,
            "view_mode": "form",
            "target": "current",
        }
