# Copyright 2026 Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError


class L10nClDteReconciliationWizard(models.TransientModel):
    _name = "l10n_cl.dte.reconciliation.wizard"
    _description = "Conciliar envío DTE incierto"

    move_id = fields.Many2one("account.move", required=True, readonly=True)
    resolution = fields.Selection(
        [
            ("received", "El SII recibió el DTE; consultar su TrackID"),
            ("not_received", "El SII no lo recibió; autorizar reenvío"),
        ],
        required=True,
    )
    track_id = fields.Char(string="TrackID SII")
    evidence = fields.Text(
        required=True,
        help="Registre la fuente consultada y la evidencia que respalda la decisión.",
    )

    def action_confirm(self):
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_sii.group_dte_manager"):
            raise AccessError(
                _("Sólo un responsable DTE puede conciliar un envío incierto.")
            )
        if not self.move_id:
            raise UserError(_("Seleccione la factura DTE que debe conciliar."))
        self.env["l10n_cl.dte.reconciliation"].resolve_uncertain_submission(
            self.move_id,
            self.resolution,
            self.track_id,
            self.evidence,
        )
        return {"type": "ir.actions.act_window_close"}
