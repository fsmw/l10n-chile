# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models
from odoo.exceptions import UserError, ValidationError


class DteAssignmentAttempt(models.Model):
    _name = "l10n_cl.dte.assignment.attempt"
    _description = "DTE Assignment RTC Attempt"
    _order = "started_at desc, id desc"
    _check_company_auto = True

    assignment_id = fields.Many2one(
        "l10n_cl.dte.assignment", required=True, index=True, ondelete="restrict"
    )
    company_id = fields.Many2one(
        related="assignment_id.company_id", store=True, index=True
    )
    operation = fields.Selection(
        [
            ("send", "Upload AEC"),
            ("query", "Query Upload Status"),
            ("reconcile", "Query Assignment Registry"),
        ],
        required=True,
        index=True,
    )
    state = fields.Selection(
        [
            ("started", "Started"),
            ("completed", "Completed"),
            ("failed", "Failed Before Dispatch"),
            ("unknown", "Outcome Unknown"),
        ],
        required=True,
        default="started",
        index=True,
    )
    started_at = fields.Datetime(
        default=fields.Datetime.now, required=True, readonly=True
    )
    completed_at = fields.Datetime(readonly=True)
    request_digest = fields.Char(index=True, copy=False)
    response_digest = fields.Char(index=True, copy=False)
    error_code = fields.Char()
    error_summary = fields.Text()

    def write(self, vals):
        """Allow one completion; the request identity stays fixed."""
        allowed = {"state", "completed_at", "response_digest", "error_code"}
        if set(vals) - allowed - {"error_summary"}:
            raise UserError(self.env._("Attempt request data is immutable."))
        if vals.get("state") not in {"completed", "failed", "unknown"}:
            raise ValidationError(self.env._("An attempt can only be completed once."))
        self.flush_recordset()
        self.env.cr.execute(
            "SELECT id FROM l10n_cl_dte_assignment_attempt "
            "WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
            [self.ids],
        )
        self.invalidate_recordset()
        if any(attempt.state != "started" for attempt in self):
            raise UserError(self.env._("A completed attempt cannot be changed."))
        return super().write({"completed_at": fields.Datetime.now(), **vals})

    def _complete(self, state, response_digest=None, error_code=None, summary=None):
        self.ensure_one()
        return self.write(
            {
                "state": state,
                "response_digest": response_digest or False,
                "error_code": (error_code or "")[:128] or False,
                "error_summary": (summary or "")[:4000] or False,
            }
        )

    def unlink(self):  # pylint: disable=method-required-super
        raise UserError(self.env._("RTC attempts are retained as audit evidence."))
