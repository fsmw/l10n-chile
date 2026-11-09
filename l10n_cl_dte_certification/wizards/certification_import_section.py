# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class DteCertificationImportWizardSection(models.TransientModel):
    _name = "l10n_cl.dte.certification.import.wizard.section"
    _description = "Detected SII Certification Set Section"
    _order = "sequence, id"
    _rec_name = "name"

    wizard_id = fields.Many2one(
        "l10n_cl.dte.certification.import.wizard",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(required=True)
    name = fields.Char(compute="_compute_name")
    attention_number = fields.Char(required=True, readonly=True)
    case_count = fields.Integer(readonly=True)
    document_summary = fields.Char(readonly=True)

    @api.depends("attention_number", "case_count", "document_summary")
    def _compute_name(self):
        for section in self:
            section.name = " — ".join(
                value
                for value in (
                    section.attention_number,
                    str(section.case_count) if section.case_count else "0",
                    section.document_summary,
                )
                if value
            )

    @api.constrains("wizard_id", "attention_number")
    def _check_unique_attention(self):
        for section in self:
            other_sections = self.search(
                [
                    ("wizard_id", "=", section.wizard_id.id),
                    ("attention_number", "=", section.attention_number),
                    ("id", "!=", section.id),
                ]
            )
            if other_sections:
                raise ValidationError(_("SET attention numbers must be unique."))
