# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.exceptions import UserError

from .dte_assignment import ACTIVE_STATES, IN_PROGRESS_STATES, REAL_ENVIRONMENTS

_DTE_USER = "l10n_cl_dte_core.group_dte_user"


class AccountMove(models.Model):
    _inherit = "account.move"

    dte_assignment_ids = fields.One2many(
        "l10n_cl.dte.assignment",
        "move_id",
        string="DTE Assignments",
        groups=_DTE_USER,
        copy=False,
    )
    dte_assignment_state = fields.Selection(
        [
            ("none", "Not Assigned"),
            ("in_progress", "Assignment in Progress"),
            ("assigned", "Assigned"),
        ],
        string="Factoring Assignment",
        compute="_compute_dte_assignment",
        groups=_DTE_USER,
    )
    dte_assignee_id = fields.Many2one(
        "res.partner",
        string="Factoring Company",
        compute="_compute_dte_assignment",
        groups=_DTE_USER,
    )
    dte_assignment_track_id = fields.Char(
        string="Assignment TrackID",
        compute="_compute_dte_assignment",
        groups=_DTE_USER,
    )
    dte_assignment_available = fields.Boolean(
        compute="_compute_dte_assignment", groups=_DTE_USER
    )

    @api.depends("dte_assignment_ids.state", "dte_document_ids.fiscal_state", "state")
    def _compute_dte_assignment(self):
        for move in self:
            current = move.dte_assignment_ids.filtered(
                lambda item: item.state in ACTIVE_STATES
            )[:1]
            if current.state == "accepted":
                move.dte_assignment_state = "assigned"
            elif current.state in IN_PROGRESS_STATES:
                move.dte_assignment_state = "in_progress"
            else:
                move.dte_assignment_state = "none"
            move.dte_assignee_id = current.partner_id
            move.dte_assignment_track_id = current.track_id
            move.dte_assignment_available = (
                not current
                and self.env.user.has_group("l10n_cl_dte_core.group_dte_manager")
                and bool(move._find_dte_assignable_document())
            )

    def _find_dte_assignable_document(self):
        """Return the accepted DTE 33/34 this invoice can assign, if any."""
        self.ensure_one()
        code = self.l10n_latam_document_type_id.code
        if (
            self.state != "posted"
            or self.move_type != "out_invoice"
            or code not in self.env["l10n_cl.dte.assignment"]._assignable_codes()
            or self.payment_state in {"paid", "in_payment", "reversed"}
        ):
            return self.env["l10n_cl.electronic.document"]
        return self._get_dte_source_documents().filtered(
            lambda document: document.document_code == code
            and document.fiscal_state == "accepted"
            and document.sii_environment in REAL_ENVIRONMENTS
            and document.sii_environment == self.company_id.dte_sii_environment
        )

    def _get_dte_assignable_document(self, ignore=None):
        """Return the DTE to assign or explain why the invoice is not eligible."""
        self.ensure_one()
        documents = self._find_dte_assignable_document()
        if len(documents) != 1:
            raise UserError(
                self.env._(
                    "Only a posted, unpaid customer invoice (DTE 33 or 34) "
                    "accepted by SII in Certification or Production can be "
                    "assigned."
                )
            )
        others = (
            self.env["l10n_cl.dte.assignment"]
            .sudo()
            .search(
                [
                    ("document_id", "=", documents.id),
                    ("state", "in", ACTIVE_STATES),
                    (
                        "id",
                        "not in",
                        (ignore or self.env["l10n_cl.dte.assignment"]).ids,
                    ),
                ],
                limit=1,
            )
        )
        if others:
            raise UserError(
                self.env._("This invoice already has an assignment in progress.")
            )
        return documents

    def action_create_dte_assignment(self):
        """Open a new factoring assignment for this invoice."""
        self.ensure_one()
        self.env["l10n_cl.dte.assignment"]._check_manager()
        self._get_dte_assignable_document()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Assign to Factoring"),
            "res_model": "l10n_cl.dte.assignment",
            "view_mode": "form",
            "target": "current",
            "context": {"default_move_id": self.id},
        }

    def action_view_dte_assignments(self):
        """Show the assignments of this invoice."""
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "l10n_cl_dte_factoring.action_dte_assignment"
        )
        action["domain"] = [("move_id", "=", self.id)]
        assignments = self.dte_assignment_ids
        if len(assignments) == 1:
            action.update({"views": [(False, "form")], "res_id": assignments.id})
        return action
