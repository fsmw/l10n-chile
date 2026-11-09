# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class DteCertificationReceiverActivityCorrectionWizard(models.TransientModel):
    _name = "l10n_cl.dte.certification.receiver.activity.wizard"
    _description = "Correct Certification Receiver Activity"
    _check_company_auto = True

    case_id = fields.Many2one(
        "l10n_cl.dte.certification.case", required=True, ondelete="cascade"
    )
    company_id = fields.Many2one(related="case_id.company_id", readonly=True)
    previous_activity = fields.Char(required=True)
    corrected_activity = fields.Char(required=True)
    verification_reference = fields.Char(required=True)
    correction_preview = fields.Text(compute="_compute_correction_preview")

    @api.depends("previous_activity", "corrected_activity")
    def _compute_correction_preview(self):
        for wizard in self:
            wizard.correction_preview = (
                f"Donde dice: Giro del receptor: {wizard.previous_activity or ''}\n"
                f"Debe decir: Giro del receptor: {wizard.corrected_activity or ''}"
            )

    def action_apply(self):
        self.ensure_one()
        self.case_id._apply_receiver_activity_correction(
            self.previous_activity,
            self.corrected_activity,
            self.verification_reference,
        )
        return {"type": "ir.actions.act_window_close"}
