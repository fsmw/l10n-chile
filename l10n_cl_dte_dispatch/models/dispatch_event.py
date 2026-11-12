# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class DteDispatchEvent(models.Model):
    _name = "l10n_cl.dte.dispatch.event"
    _description = "Immutable electronic dispatch guide event"
    _order = "event_date, id"

    dispatch_id = fields.Many2one(
        "l10n_cl.dte.dispatch",
        required=True,
        ondelete="restrict",
        index=True,
    )
    event_type = fields.Selection(
        [("prepared", "Prepared"), ("submitted", "Submitted"), ("result", "Result")],
        required=True,
    )
    event_date = fields.Datetime(required=True, default=fields.Datetime.now)
    payload_digest = fields.Char(required=True, index=True)
    details = fields.Text()
