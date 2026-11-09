# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class DteCertificationRequirementTemplate(models.Model):
    _name = "l10n_cl.dte.certification.requirement.template"
    _description = "SII DTE Certification Requirement Template"
    _order = "stage_code, sequence, id"
    _sql_constraints = [
        (
            "stage_requirement_revision_uniq",
            "unique(stage_code, code, normative_version)",
            "A requirement template revision must be unique.",
        ),
    ]

    stage_code = fields.Selection(
        [
            ("assigned_set", "Assigned Test Set"),
            ("simulation_set", "Simulation Set"),
            ("information_exchange", "Information Exchange"),
            ("printed_samples", "Printed Samples"),
            ("compliance_declaration", "Compliance Declaration"),
            ("issuer_registration", "Electronic Issuer Registration"),
        ],
        required=True,
        index=True,
    )
    code = fields.Char(required=True, index=True)
    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(required=True, default=10)
    mandatory = fields.Boolean(required=True, default=True)
    external_result = fields.Boolean(
        help="This requirement is satisfied only by a verified external result.",
    )
    applicability = fields.Selection(
        [
            ("required", "Required"),
            ("conditional", "Conditional"),
        ],
        required=True,
        default="required",
    )
    normative_title = fields.Char(required=True)
    normative_url = fields.Char(required=True)
    normative_version = fields.Char(required=True, index=True)
    normative_sha256 = fields.Char()
    active = fields.Boolean(default=True, index=True)
