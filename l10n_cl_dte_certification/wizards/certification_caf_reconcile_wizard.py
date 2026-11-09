# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Reconcile a CAF recovered manually from the SII portal."""

from odoo import _, fields, models
from odoo.exceptions import AccessError


class CertificationCafReconcileWizard(models.TransientModel):
    _name = "l10n_cl.dte.certification.caf.reconcile.wizard"
    _description = "Reconcile Authorized Folios"

    line_id = fields.Many2one(
        "l10n_cl.dte.certification.caf.request.line", required=True, readonly=True
    )
    caf_id = fields.Many2one(
        "l10n_cl.caf.authorization",
        required=True,
        domain="[('company_id', '=', line_id.company_id), "
        "('sii_environment', '=', 'certification'), "
        "('is_synthetic', '=', False), ('state', 'in', ['validated', 'exhausted'])]",
    )
    note = fields.Char(required=True, size=500)

    def action_reconcile(self):
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(_("Only a DTE manager can reconcile CAF requests."))
        from ..services.certification_caf_service import CertificationCafService

        CertificationCafService(self.env).reconcile_line(
            self.line_id, self.caf_id, self.note
        )
        return {"type": "ir.actions.act_window_close"}
