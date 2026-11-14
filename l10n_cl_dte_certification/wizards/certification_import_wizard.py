# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models


class DteCertificationImportWizard(models.TransientModel):
    _name = "l10n_cl.dte.certification.import.wizard"
    _description = "Import Original SII Certification Set"

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    source_file = fields.Binary(required=True, attachment=False)
    source_filename = fields.Char(required=True)

    def action_import(self):
        self.ensure_one()
        certification_set = self.env[
            "l10n_cl.dte.certification.set"
        ]._create_from_import(self.company_id, self.source_filename, self.source_file)
        return {
            "type": "ir.actions.act_window",
            "name": _("Certification Set Source"),
            "res_model": "l10n_cl.dte.certification.set",
            "res_id": certification_set.id,
            "view_mode": "form",
            "target": "current",
        }

    source_encoding = fields.Selection(
        [("utf-8", "UTF-8"), ("iso-8859-1", "ISO-8859-1")],
        required=True,
        default="utf-8",
    )

    parser_profile = fields.Selection(
        [
            ("sii_txt_v1", "SII text v1"),
            ("reviewed_mapping_v1", "Reviewed mapping v1"),
            ("sii_section_v1", "SII section v1"),
            ("reviewed_section_v1", "Reviewed section v1"),
            ("sii_txt_v2", "SII text v2"),
            ("reviewed_mapping_v2", "Reviewed mapping v2"),
            ("sii_section_v2", "SII section v2"),
            ("reviewed_section_v2", "Reviewed section v2"),
        ],
        required=True,
        default="sii_txt_v2",
    )

    selected_section_id = fields.Many2one(
        "l10n_cl.dte.certification.import.wizard.section",
        string="SET attention number",
        domain="[('wizard_id', '=', id), ('case_count', '>', 0)]",
    )

    section_option_ids = fields.One2many(
        "l10n_cl.dte.certification.import.wizard.section",
        "wizard_id",
        string="Detected SET sections",
    )

    mapping_file = fields.Binary(attachment=False)

    mapping_filename = fields.Char()

    accounting_warning_acknowledged = fields.Boolean(
        string="I am using an isolated test database"
    )
