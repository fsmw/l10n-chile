# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class DteCertificationRunWizard(models.TransientModel):
    _name = "l10n_cl.dte.certification.run.wizard"
    _description = "Create SII Certification Execution"

    set_id = fields.Many2one(
        "l10n_cl.dte.certification.set",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    case_ids = fields.Many2many(
        "l10n_cl.dte.certification.case",
        relation="l10n_cl_dte_cert_run_wiz_case_rel",
        string="Cases to include",
        domain="[('set_id', '=', set_id)]",
        required=True,
    )

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        set_id = values.get("set_id") or self.env.context.get("active_id")
        if set_id and "case_ids" in field_names:
            record = self.env["l10n_cl.dte.certification.set"].browse(set_id)
            values["case_ids"] = [fields.Command.set(record.case_ids.ids)]
        if set_id and "set_id" in field_names:
            values["set_id"] = set_id
        return values

    def action_create_execution(self):
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(_("Only DTE managers can create certification executions."))
        run = self.env["l10n_cl.dte.certification.run"]._create_from_selection(
            self.set_id, self.case_ids
        )
        return run.action_open()
