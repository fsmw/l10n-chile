# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class DteCertificationInterpretationEvent(models.Model):
    _name = "l10n_cl.dte.certification.interpretation.event"
    _description = "SII Certification Case Interpretation Event"
    _order = "sequence desc, id desc"
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
    from_interpreter_version = fields.Char(required=True, readonly=True)
    to_interpreter_version = fields.Char(required=True, readonly=True)
    previous_interpretation = fields.Json(
        required=True, readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    new_interpretation = fields.Json(
        required=True, readonly=True, groups="l10n_cl_dte_core.group_dte_manager"
    )
    actor_id = fields.Many2one(
        "res.users", required=True, readonly=True, default=lambda self: self.env.user
    )
    occurred_at = fields.Datetime(
        required=True, readonly=True, default=fields.Datetime.now
    )

    _sql_constraints = [
        (
            "case_interpretation_event_sequence_uniq",
            "unique(case_id, sequence)",
            "Interpretation event sequence must be unique within a case.",
        )
    ]

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super
        raise UserError(_("Interpretation events are created by the update action."))

    def write(self, vals):  # pylint: disable=method-required-super
        raise UserError(_("Interpretation events are immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("Interpretation events cannot be deleted."))

    @api.model
    def _create_from_reinterpretation(
        self,
        case,
        from_version,
        previous_interpretation,
        to_version,
        new_interpretation,
    ):
        sequence = (
            max(case.interpretation_history_ids.mapped("sequence"), default=0) + 1
        )
        values = {
            "case_id": case.id,
            "sequence": sequence,
            "from_interpreter_version": from_version,
            "to_interpreter_version": to_version,
            "previous_interpretation": previous_interpretation or {},
            "new_interpretation": new_interpretation,
            "actor_id": self.env.user.id,
            "occurred_at": fields.Datetime.now(),
        }
        return super(DteCertificationInterpretationEvent, self.sudo()).create(values)
