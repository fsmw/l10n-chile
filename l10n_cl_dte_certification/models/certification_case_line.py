# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class DteCertificationCaseLine(models.Model):
    _name = "l10n_cl.dte.certification.case.line"
    _description = "SII Certification Case Detail Snapshot"
    _order = "sequence, id"
    _check_company_auto = True

    case_id = fields.Many2one(
        "l10n_cl.dte.certification.case",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    set_id = fields.Many2one(
        related="case_id.set_id", store=True, readonly=True, index=True
    )
    company_id = fields.Many2one(
        related="case_id.company_id", store=True, readonly=True, index=True
    )
    sequence = fields.Integer(required=True, readonly=True)
    source_line = fields.Integer(required=True, readonly=True, index=True)
    item_name = fields.Char(
        required=True,
        readonly=True,
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    quantity = fields.Char(readonly=True, groups="l10n_cl_dte_core.group_dte_manager")
    unit_price = fields.Char(readonly=True, groups="l10n_cl_dte_core.group_dte_manager")
    discount_percent = fields.Char(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    unit_of_measure = fields.Char(
        readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    source_digest = fields.Char(required=True, readonly=True, index=True)
    interpretation_version = fields.Char(required=True, readonly=True)

    _sql_constraints = [
        (
            "case_line_sequence_uniq",
            "unique(case_id, sequence)",
            "Detail sequence must be unique within a case.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super
        raise UserError(_("Case detail snapshots are created from interpretation."))

    def write(self, vals):  # pylint: disable=method-required-super
        raise UserError(_("Certification case detail snapshots are immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("Certification case detail snapshots cannot be deleted."))

    @api.model
    def _create_from_interpretation(self, case, rows, interpretation_version):
        values = [
            {
                "case_id": case.id,
                "sequence": row["sequence"],
                "source_line": row["source_line"],
                "item_name": row["name"],
                "quantity": row.get("quantity"),
                "unit_price": row.get("unit_price"),
                "discount_percent": row.get("discount_percent"),
                "unit_of_measure": row.get("uom"),
                "source_digest": case.source_digest,
                "interpretation_version": interpretation_version,
            }
            for row in rows
        ]
        return super(DteCertificationCaseLine, self.sudo()).create(values)
