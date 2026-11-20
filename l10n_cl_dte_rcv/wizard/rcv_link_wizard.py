# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models
from odoo.exceptions import UserError


class RcvLinkWizard(models.TransientModel):
    """Link or unlink an RCV document and a journal entry, with a reason."""

    _name = "l10n_cl.dte.rcv.link.wizard"
    _description = "Link RCV Document"

    document_id = fields.Many2one(
        "l10n_cl.dte.rcv.document", required=True, readonly=True, ondelete="cascade"
    )
    mode = fields.Selection(
        [("link", "Link"), ("unlink", "Unlink")],
        required=True,
        default="link",
        readonly=True,
    )
    company_id = fields.Many2one(related="document_id.company_id")
    current_move_id = fields.Many2one(
        related="document_id.move_id", string="Current Entry"
    )
    candidate_move_ids = fields.Many2many(
        "account.move", compute="_compute_candidate_move_ids"
    )
    move_id = fields.Many2one("account.move", string="Journal Entry")
    reason = fields.Text(required=True)

    def _compute_candidate_move_ids(self):
        for wizard in self:
            wizard.candidate_move_ids = (
                wizard.document_id._get_rcv_candidate_moves()
                if wizard.document_id
                else False
            )

    def action_confirm(self):
        self.ensure_one()
        reason = (self.reason or "").strip()
        if not reason:
            raise UserError(self.env._("Explain the review behind this decision."))
        if self.mode == "unlink":
            if not self.document_id.move_id:
                raise UserError(
                    self.env._("The RCV document is not linked to a journal entry.")
                )
            self.document_id._unlink_move(reason)
        else:
            if not self.move_id:
                raise UserError(self.env._("Select the journal entry to link."))
            self.document_id._link_move(self.move_id, reason)
        return {"type": "ir.actions.act_window_close"}
