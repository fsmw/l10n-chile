# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class DteCertificationReopenStageWizard(models.TransientModel):
    _name = "l10n_cl.dte.certification.reopen.stage.wizard"
    _description = "Reopen SII DTE Certification Stage"

    process_id = fields.Many2one(
        "l10n_cl.dte.certification.process",
        required=True,
        ondelete="cascade",
    )
    stage_id = fields.Many2one(
        "l10n_cl.dte.certification.process.stage",
        required=True,
        ondelete="cascade",
    )
    reason = fields.Text(required=True)

    def action_apply(self):
        """Reopen a failed stage while retaining its previous attempts."""
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(_("Only a DTE manager can reopen a certification stage."))
        process = self.process_id
        process._check_process_access()
        stage = self.stage_id
        if stage.process_id != process:
            raise ValidationError(_("The stage must belong to the selected process."))
        if stage.state != "rejected" or process.current_stage_code != stage.code:
            raise UserError(_("Only the current rejected stage can be reopened."))
        reason = (self.reason or "").strip()
        if len(reason) < 12:
            raise ValidationError(
                _("Provide a specific reason of at least 12 characters.")
            )
        stage._write_internal(
            {
                "state": "in_progress",
                "sii_status": "reopened_after_rejection",
                "diagnostics": [{"code": "reopened", "reason": reason[:1000]}],
            }
        )
        process._record_event(
            "stage_reopened",
            stage=stage,
            from_state="rejected",
            to_state="in_progress",
            details={"reason": reason[:1000]},
        )
        return {"type": "ir.actions.act_window_close"}
