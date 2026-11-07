# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class DteCertificationGenerationWizard(models.TransientModel):
    _name = "l10n_cl.dte.certification.generation.wizard"
    _description = "Generate SII Certification Drafts"

    run_id = fields.Many2one(
        "l10n_cl.dte.certification.run", required=True, readonly=True
    )
    company_id = fields.Many2one(related="run_id.company_id", readonly=True)
    journal_id = fields.Many2one(
        "account.journal",
        required=True,
        domain=(
            "[('company_id', '=', company_id), ('type', '=', 'sale'), "
            "('l10n_latam_use_documents', '=', True)]"
        ),
    )
    vat_tax_id = fields.Many2one(
        "account.tax",
        domain=(
            "[('company_id', '=', company_id), ('type_tax_use', '=', 'sale'), "
            "('amount', '=', 19)]"
        ),
        string="19% VAT",
    )
    income_account_id = fields.Many2one(
        "account.account",
        required=True,
        domain=(
            "[('company_ids', 'in', [company_id]), ('account_type', '=', 'income'), "
            "('deprecated', '=', False)]"
        ),
        string="Sales income account",
    )
    line_ids = fields.One2many(
        "l10n_cl.dte.certification.generation.wizard.line", "wizard_id"
    )

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        run_id = values.get("run_id") or self.env.context.get("default_run_id")
        if run_id:
            run = self.env["l10n_cl.dte.certification.run"].browse(run_id)
            if "line_ids" in field_names:
                values["line_ids"] = [
                    fields.Command.create(
                        {
                            "run_line_id": line.id,
                            "selected": line.document_code in {"33", "34"}
                            and line.generation_state == "pending",
                        }
                    )
                    for line in run.line_ids
                ]
        return values

    def action_generate(self):
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise UserError(
                _("Only DTE managers can generate certification documents.")
            )
        from ..services.case_generation_service import CaseGenerationService

        selected = self.line_ids.filtered("selected")
        CaseGenerationService(self.env).generate(
            self.run_id,
            journal=self.journal_id,
            vat_tax=self.vat_tax_id,
            income_account=self.income_account_id,
            partner_by_line={line.run_line_id.id: line.partner_id for line in selected},
            selected_lines=selected.mapped("run_line_id"),
        )
        return self.run_id.action_open()


class DteCertificationGenerationWizardLine(models.TransientModel):
    _name = "l10n_cl.dte.certification.generation.wizard.line"
    _description = "SII Certification Draft Mapping"
    _order = "sequence, id"

    wizard_id = fields.Many2one(
        "l10n_cl.dte.certification.generation.wizard", required=True, ondelete="cascade"
    )
    run_line_id = fields.Many2one(
        "l10n_cl.dte.certification.run.line", required=True, readonly=True
    )
    sequence = fields.Integer(related="run_line_id.sequence", readonly=True)
    case_identifier = fields.Char(related="run_line_id.case_identifier", readonly=True)
    document_code = fields.Char(related="run_line_id.document_code", readonly=True)
    selected = fields.Boolean(default=True)
    partner_id = fields.Many2one(
        "res.partner",
        domain="[('company_id', 'in', [False, wizard_id.company_id])]",
    )
