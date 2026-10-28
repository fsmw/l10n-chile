# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import _, fields, models
from odoo.exceptions import UserError


class RcvLinkWizard(models.TransientModel):
    _name = "l10n.cl.rcv.link.wizard"
    _description = "Manually link an RCV document to an Odoo move"

    document_id = fields.Many2one("l10n.cl.rcv.document", required=True, readonly=True)
    candidate_move_ids = fields.Many2many(related="document_id.candidate_move_ids")
    move_id = fields.Many2one("account.move", required=True)
    reason = fields.Text(required=True)

    def action_confirm(self):
        self.ensure_one()
        if self.move_id.company_id != self.document_id.company_id:
            raise UserError(_("La factura debe pertenecer a la misma compañía."))
        if self.move_id not in self.candidate_move_ids:
            raise UserError(
                _("La factura no coincide con la identidad fiscal del documento RCV.")
            )
        self.env["l10n.cl.rcv.match.log"].create(
            {
                "document_id": self.document_id.id,
                "previous_move_id": self.document_id.move_id.id,
                "move_id": self.move_id.id,
                "reason": self.reason,
            }
        )
        self.document_id.write(
            {"move_id": self.move_id.id, "reconciliation_state": "review"}
        )
        return {"type": "ir.actions.act_window_close"}
