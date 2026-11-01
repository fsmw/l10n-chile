from odoo import _, fields, models
from odoo.exceptions import UserError, ValidationError


class SubmissionAttempt(models.Model):
    _name = "l10n_cl.submission.attempt"
    _description = "DTE SII Transport Attempt"
    _order = "started_at desc, id desc"
    _check_company_auto = True

    submission_id = fields.Many2one(
        "l10n_cl.electronic.submission", required=True, index=True, ondelete="restrict"
    )
    company_id = fields.Many2one(
        related="submission_id.company_id", store=True, index=True
    )
    operation = fields.Selection(
        [
            ("send", "Send"),
            ("query", "Query Status"),
            ("reconcile", "Reconcile Outcome"),
        ],
        required=True,
        index=True,
    )
    state = fields.Selection(
        [
            ("started", "Started"),
            ("completed", "Completed"),
            ("failed", "Failed"),
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
        """Allow one monotonic completion while keeping request identity fixed."""
        allowed = {
            "state",
            "completed_at",
            "response_digest",
            "error_code",
            "error_summary",
        }
        if set(vals) - allowed:
            raise UserError(
                _("Transport attempt identity and request data are immutable.")
            )
        if vals.get("state") not in {"completed", "failed", "unknown"}:
            raise ValidationError(_("A transport attempt can only be completed once."))
        for attempt in self:
            if attempt.state != "started":
                raise UserError(_("A completed transport attempt cannot be changed."))
        values = vals.copy()
        values.setdefault("completed_at", fields.Datetime.now())
        return super().write(values)

    def _complete(
        self, state, response_digest=None, error_code=None, error_summary=None
    ):
        self.ensure_one()
        return self.write(
            {
                "state": state,
                "response_digest": response_digest or False,
                "error_code": error_code or False,
                "error_summary": error_summary or False,
            }
        )

    def unlink(self):
        # pylint: disable=method-required-super
        raise UserError(_("Transport attempts are retained as audit evidence."))
