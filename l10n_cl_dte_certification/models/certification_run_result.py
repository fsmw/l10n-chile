# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class DteCertificationRunResult(models.Model):
    _name = "l10n_cl.dte.certification.run.result"
    _description = "SII Certification Execution Result Snapshot"
    _order = "reconciled_at desc, id desc"
    _check_company_auto = True

    run_id = fields.Many2one(
        "l10n_cl.dte.certification.run",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    submission_id = fields.Many2one(
        "l10n_cl.sii.envelope.submission",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    result_import_id = fields.Many2one(
        "l10n_cl.sii.envelope.result.import",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    source_digest = fields.Char(required=True, readonly=True, index=True)
    reconciled_at = fields.Datetime(required=True, readonly=True)
    reconciled_by_id = fields.Many2one("res.users", required=True, readonly=True)
    verification_state = fields.Selection(
        related="result_import_id.verification_state", readonly=True
    )
    sii_state = fields.Char(readonly=True)
    expected_count = fields.Integer(readonly=True)
    accepted_count = fields.Integer(readonly=True)
    rejected_count = fields.Integer(readonly=True)
    repair_count = fields.Integer(readonly=True)
    indeterminate_count = fields.Integer(readonly=True)
    line_ids = fields.One2many(
        "l10n_cl.dte.certification.run.result.line", "result_id", readonly=True
    )

    _sql_constraints = [
        (
            "run_result_digest_uniq",
            "unique(run_id, source_digest)",
            "This SII result was already reconciled for the execution.",
        )
    ]

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super
        raise UserError(_("SII certification results are created by reconciliation."))

    @api.model
    def _create_from_import(self, values, line_values):
        result = super(DteCertificationRunResult, self.sudo()).create(values)
        for line in line_values:
            self.env["l10n_cl.dte.certification.run.result.line"]._create_snapshot(
                {**line, "result_id": result.id}
            )
        return result

    def write(self, values):  # pylint: disable=method-required-super
        raise UserError(_("SII certification result snapshots are immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("SII certification result snapshots are retained."))

    def action_open(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("SII Certification Result"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }


class DteCertificationRunResultLine(models.Model):
    _name = "l10n_cl.dte.certification.run.result.line"
    _description = "SII Certification Case Result Snapshot"
    _order = "sequence, id"
    _check_company_auto = True

    result_id = fields.Many2one(
        "l10n_cl.dte.certification.run.result",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    company_id = fields.Many2one(related="result_id.company_id", store=True, index=True)
    run_line_id = fields.Many2one(
        "l10n_cl.dte.certification.run.line",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    gateway_result_line_id = fields.Many2one(
        "l10n_cl.sii.envelope.result.line",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    sequence = fields.Integer(related="run_line_id.sequence", store=True, readonly=True)
    case_identifier = fields.Char(
        related="run_line_id.case_identifier", store=True, readonly=True
    )
    document_type = fields.Char(required=True, readonly=True)
    folio = fields.Char(required=True, readonly=True)
    state = fields.Selection(
        [
            ("accepted", "Reported accepted"),
            ("accepted_with_observations", "Reported with observations"),
            ("rejected", "Reported rejected"),
            ("indeterminate", "Indeterminate"),
        ],
        required=True,
        readonly=True,
    )
    sii_state_code = fields.Char(readonly=True)
    sii_detail = fields.Text(readonly=True)

    _sql_constraints = [
        (
            "result_case_uniq",
            "unique(result_id, run_line_id)",
            "An execution case can appear only once in an SII result snapshot.",
        ),
        (
            "result_gateway_line_uniq",
            "unique(result_id, gateway_result_line_id)",
            "A gateway result line can appear only once in a snapshot.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super
        raise UserError(
            _("SII certification result lines are created by reconciliation.")
        )

    @api.model
    def _create_snapshot(self, values):
        return super(DteCertificationRunResultLine, self.sudo()).create(values)

    def write(self, values):  # pylint: disable=method-required-super
        raise UserError(_("SII certification result snapshots are immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(_("SII certification result snapshots are retained."))
