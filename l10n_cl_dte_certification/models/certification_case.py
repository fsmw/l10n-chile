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
    global_discount_percent = fields.Char(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    global_discount_source_line = fields.Integer(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    reference_case_identifier = fields.Char(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    reference_source_line = fields.Integer(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    reference_reason = fields.Text(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    reference_reason_source_line = fields.Integer(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    reference_code = fields.Selection(
        [
            ("1", "1 - Cancels"),
            ("2", "2 - Corrects text"),
            ("3", "3 - Corrects amounts"),
        ],
        readonly=True,
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    reference_code_source_line = fields.Integer(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    note_operation = fields.Selection(
        [
            ("cancel", "Cancel"),
            ("return", "Return"),
            ("text", "Text correction"),
            ("amount", "Amount correction"),
        ],
        readonly=True,
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    text_correction = fields.Json(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    interpretation_history_ids = fields.One2many(
        "l10n_cl.dte.certification.interpretation.event",
        "case_id",
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    interpretation_version = fields.Char(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    interpretation = fields.Json(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    line_ids = fields.One2many(
        "l10n_cl.dte.certification.case.line",
        "case_id",
        copy=False,
        groups="l10n_cl_dte_core.group_dte_manager",
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

    def _interpret_from_instructions(self, audit_reinterpretation=False):
        CaseLine = self.env["l10n_cl.dte.certification.case.line"]
        for case in self:
            previous_version = case.interpretation_version
            previous_interpretation = case.interpretation
            if previous_version and previous_version != INTERPRETER_VERSION:
                if not audit_reinterpretation:
                    raise UserError(
                        _(
                            "This case uses an older interpreter. Use the explicit "
                            "reinterpretation action to update it."
                        )
                    )
            values = interpret(
                case.instruction_text, case.document_code, case.source_start_line
            )
            values["interpreter_version"] = INTERPRETER_VERSION
            super(DteCertificationCase, case.sudo()).write(
                {
                    "interpretation_state": values["state"],
                    "diagnostics": values["diagnostics"],
                    "global_discount_percent": values["global_discount_percent"],
                    "global_discount_source_line": values[
                        "global_discount_source_line"
                    ],
                    "reference_case_identifier": values["reference_case_identifier"],
                    "reference_source_line": values["reference_source_line"],
                    "reference_reason": values["reference_reason"],
                    "reference_reason_source_line": values[
                        "reference_reason_source_line"
                    ],
                    "reference_code": values["reference_code"],
                    "reference_code_source_line": values["reference_code_source_line"],
                    "note_operation": values["note_operation"],
                    "text_correction": values["text_correction"],
                    "interpretation_version": INTERPRETER_VERSION,
                    "interpretation": values,
                }
            )
            rows = values.get("items", [])
            if case.line_ids:
                existing = [
                    {
                        "sequence": line.sequence,
                        "source_line": line.source_line,
                        "name": line.item_name,
                        "quantity": line.quantity or None,
                        "unit_price": line.unit_price or None,
                        "discount_percent": line.discount_percent or None,
                        "uom": line.unit_of_measure or None,
                    }
                    for line in case.line_ids.sorted("sequence")
                ]
                if existing != rows:
                    raise UserError(
                        _("Interpreted details already exist and cannot be replaced.")
                    )
            elif rows:
                CaseLine._create_from_interpretation(case, rows, INTERPRETER_VERSION)
            if previous_version and previous_version != INTERPRETER_VERSION:
                case.env[
                    "l10n_cl.dte.certification.interpretation.event"
                ]._create_from_reinterpretation(
                    case,
                    previous_version,
                    previous_interpretation,
                    INTERPRETER_VERSION,
                    values,
                )
        return True
