# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models

FAILURE_PHASES = [
    ("pre_dispatch", "Before SII dispatch"),
    ("post_dispatch", "After SII dispatch"),
    ("validation", "CAF validation"),
]


class CafRequestAttempt(models.Model):
    _name = "l10n_cl.caf.request.attempt"
    _description = "SII CAF Request Attempt"
    _order = "request_id, number desc"
    _check_company_auto = True
    _sql_constraints = [
        (
            "request_number_uniq",
            "unique(request_id, number)",
            "The attempt number must be unique per CAF request.",
        ),
    ]

    request_id = fields.Many2one(
        "l10n_cl.caf.request", required=True, index=True, ondelete="cascade"
    )
    company_id = fields.Many2one(
        related="request_id.company_id", store=True, index=True
    )
    number = fields.Integer(required=True, readonly=True)
    state = fields.Selection(
        [
            ("running", "Running"),
            ("success", "Success"),
            ("partial", "Partial"),
            ("failed", "Failed"),
            ("uncertain", "Uncertain"),
        ],
        required=True,
        default="running",
        readonly=True,
    )
    executed_by = fields.Many2one("res.users", readonly=True)
    dispatch_started = fields.Boolean(
        readonly=True,
        help="Set durably right before the POST that makes the SII allocate "
        "the reserved folios.",
    )
    started_at = fields.Datetime(required=True, readonly=True)
    completed_at = fields.Datetime(readonly=True)
    endpoint = fields.Char(readonly=True)
    folio_start = fields.Integer(string="Reserved From", readonly=True)
    folio_end = fields.Integer(string="Reserved To", readonly=True)
    error_code = fields.Char(readonly=True)
    error_message = fields.Text(readonly=True)
    failure_phase = fields.Selection(FAILURE_PHASES, readonly=True)
    evidence_ids = fields.One2many(
        "l10n_cl.caf.request.evidence", "attempt_id", readonly=True
    )
