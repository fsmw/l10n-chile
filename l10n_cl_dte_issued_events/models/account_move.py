# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

from ..services.receiver_states import RECEIVER_STATES


class AccountMove(models.Model):
    _inherit = "account.move"

    dte_receiver_state = fields.Selection(
        RECEIVER_STATES,
        string="Receiver Response",
        compute="_compute_dte_receiver_state",
        help="Answer of the receiver to the latest issued DTE of this invoice.",
    )

    @api.depends("dte_document_ids")
    def _compute_dte_receiver_state(self):
        for move in self:
            # A summary badge only; the evidence stays behind DTE access rules.
            states = [
                state
                for state in move.sudo().dte_document_ids.mapped("receiver_state")
                if state != "none"
            ]
            move.dte_receiver_state = states[0] if states else "none"
