# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models

DTE_USER_GROUP = "l10n_cl_dte_core.group_dte_user"


class AccountMove(models.Model):
    _inherit = "account.move"

    dte_rcv_document_ids = fields.One2many(
        "l10n_cl.dte.rcv.document",
        "move_id",
        string="RCV Documents",
        groups=DTE_USER_GROUP,
    )
    dte_rcv_document_count = fields.Integer(
        string="RCV Document Count",
        compute="_compute_dte_rcv_document_count",
        groups=DTE_USER_GROUP,
    )
    dte_rcv_state = fields.Selection(
        [
            ("not_checked", "Not Checked"),
            ("matched", "Reported in the RCV"),
            ("not_in_sii", "Not Reported in the RCV"),
            ("indeterminate", "RCV Incomplete"),
        ],
        string="RCV State",
        default="not_checked",
        copy=False,
        readonly=True,
        index=True,
        help="Whether the last read of the RCV period reports this document. "
        "It is only reported as absent when every SII state of the period was "
        "read completely.",
    )
    dte_rcv_sync_id = fields.Many2one(
        "l10n_cl.dte.rcv.sync",
        string="Last RCV Check",
        copy=False,
        readonly=True,
        index=True,
        ondelete="set null",
        check_company=True,
        groups=DTE_USER_GROUP,
    )

    def _compute_dte_rcv_document_count(self):
        for move in self:
            move.dte_rcv_document_count = len(move.dte_rcv_document_ids)

    def action_view_dte_rcv_documents(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "l10n_cl_dte_rcv.action_rcv_document"
        )
        action["domain"] = [("move_id", "=", self.id)]
        action["context"] = {"create": False}
        return action
