# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services.case_instruction_interpreter import INTERPRETER_VERSION, interpret


class DteCertificationCase(models.Model):
    _name = "l10n_cl.dte.certification.case"
    _description = "SII Certification Case Snapshot"
    _order = "sequence, id"
    _check_company_auto = True
    _rec_name = "case_identifier"

    set_id = fields.Many2one(
        "l10n_cl.dte.certification.set",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, index=True
    )
    attention_number = fields.Char(required=True, readonly=True, index=True)
    sequence = fields.Integer(required=True, readonly=True)
    section_sequence = fields.Integer(required=True, readonly=True)
    case_identifier = fields.Char(required=True, readonly=True, index=True)
    document_code = fields.Char(required=True, readonly=True, index=True)
    source_start_line = fields.Integer(required=True, readonly=True)
    source_end_line = fields.Integer(required=True, readonly=True)
    instruction_text = fields.Text(
        required=True,
        readonly=True,
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    source_digest = fields.Char(required=True, readonly=True, index=True)
    interpretation_state = fields.Selection(
        [
            ("identified", "Identified"),
            ("unsupported", "Unsupported"),
            ("interpreted", "Interpreted"),
            ("review_required", "Review Required"),
        ],
        required=True,
        readonly=True,
    )
    diagnostics = fields.Json(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    interpretation_version = fields.Char(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    interpretation = fields.Json(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )

    _sql_constraints = [
        (
            "set_attention_case_uniq",
            "unique(set_id, attention_number, case_identifier)",
            "Case identifiers must be unique within a SET attention number.",
        ),
        (
            "set_case_sequence_uniq",
            "unique(set_id, sequence)",
            "Case positions must be unique within a SET.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super
        raise UserError(_("Cases are created from the analyzed original SET only."))

    def write(self, vals):  # pylint: disable=method-required-super
        raise UserError(_("Certification case snapshots are immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("Certification case snapshots cannot be deleted."))

    @api.model
    def _create_from_structure(self, vals_list):
        return super(DteCertificationCase, self.sudo()).create(vals_list)

    def _interpret_from_instructions(self):
        for case in self:
            values = interpret(
                case.instruction_text, case.document_code, case.source_start_line
            )
            values["interpreter_version"] = INTERPRETER_VERSION
            super(DteCertificationCase, case.sudo()).write(
                {
                    "interpretation_state": values["state"],
                    "interpretation_version": INTERPRETER_VERSION,
                    "interpretation": values,
                }
            )
        return True
