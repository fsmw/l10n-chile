# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, models
from odoo.exceptions import UserError


class ElectronicDocument(models.Model):
    _inherit = "l10n_cl.electronic.document"

    def action_create_print_sample(self):
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(_("Only a DTE manager can create print samples."))
        sample = self.env["l10n_cl.dte.report.sample"].search(
            [("document_id", "=", self.id)], limit=1
        )
        if not sample:
            sample = self.env["l10n_cl.dte.report.sample"].create(
                {"document_id": self.id, "name": self.name}
            )
        return {
            "type": "ir.actions.act_window",
            "res_model": "l10n_cl.dte.report.sample",
            "res_id": sample.id,
            "view_mode": "form",
            "target": "current",
        }
