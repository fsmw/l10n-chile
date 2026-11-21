# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from ..services.receiver_states import (
    EVENT_CODE_RE,
    RECEIVER_STATES,
    event_state,
    is_claim,
)


class DteReceiverEvent(models.Model):
    _name = "l10n_cl.dte.receiver.event"
    _description = "Receiver Response Event on an Issued DTE"
    _order = "event_date desc, id desc"
    _check_company_auto = True

    document_id = fields.Many2one(
        "l10n_cl.electronic.document",
        required=True,
        index=True,
        ondelete="restrict",
        readonly=True,
    )
    company_id = fields.Many2one(
        related="document_id.company_id", store=True, index=True
    )
    source = fields.Selection(
        [("sii", "SII Registry"), ("email", "Emailed Response")],
        required=True,
        readonly=True,
        index=True,
    )
    event_code = fields.Char(required=True, readonly=True, index=True)
    description = fields.Char(readonly=True)
    event_date_key = fields.Char(
        string="Event Date (as Received)", required=True, readonly=True
    )
    event_date = fields.Datetime(readonly=True)
    actor_vat = fields.Char(string="Actor RUT", readonly=True)
    response_id = fields.Many2one(
        "l10n_cl.dte.receiver.response",
        string="Emailed Response",
        readonly=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    receiver_state = fields.Selection(
        RECEIVER_STATES, compute="_compute_receiver_state"
    )
    is_claim = fields.Boolean(compute="_compute_receiver_state")

    _sql_constraints = [
        (
            "receiver_event_uniq",
            "unique(document_id, source, event_code, event_date_key)",
            "This receiver event is already recorded on the DTE.",
        )
    ]

    @api.depends("source", "event_code")
    def _compute_receiver_state(self):
        for event in self:
            event.receiver_state = event_state(event.source, event.event_code)
            event.is_claim = is_claim(event.source, event.event_code)

    @api.model_create_multi
    def create(self, vals_list):  # pylint: disable=method-required-super  # noqa: ARG002
        raise AccessError(self.env._("Receiver events are recorded by DTE actions."))

    @api.model
    def _create_internal(self, vals_list):
        for vals in vals_list:
            if not EVENT_CODE_RE.fullmatch(vals.get("event_code") or ""):
                raise ValidationError(self.env._("The receiver event code is invalid."))
        return super(DteReceiverEvent, self.sudo()).create(vals_list)

    def write(self, vals):  # pylint: disable=method-required-super  # noqa: ARG002
        raise AccessError(self.env._("Receiver events are immutable."))

    def unlink(self):  # pylint: disable=method-required-super
        raise AccessError(self.env._("Receiver events must be retained."))
