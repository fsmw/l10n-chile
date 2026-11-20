# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

MOVE_TYPES = {"111": "out_invoice", "112": "out_refund"}
TEXT_CORRECTION = "2"


class ExportAdjustmentWizard(models.TransientModel):
    """Prepare a draft export note 111/112 from an accepted export 110."""

    _name = "l10n_cl.dte.export.adjustment.wizard"
    _description = "Export Note Wizard"
    _check_company_auto = True

    move_id = fields.Many2one(
        "account.move", string="Export Invoice", required=True, check_company=True
    )
    company_id = fields.Many2one(related="move_id.company_id")
    target_document_id = fields.Many2one(
        "l10n_cl.electronic.document",
        string="Accepted Export DTE",
        compute="_compute_target_document_id",
    )
    document_code = fields.Selection(
        [("112", "Credit note (112)"), ("111", "Debit note (111)")],
        required=True,
        default="112",
    )
    correction = fields.Selection(
        [("1", "Cancel export"), ("2", "Correct text"), ("3", "Correct amounts")],
        required=True,
        default="3",
    )
    reason = fields.Char(required=True, size=90)

    @api.depends("move_id")
    def _compute_target_document_id(self):
        for wizard in self:
            wizard.target_document_id = wizard.move_id.sudo().dte_document_ids.filtered(
                lambda document: document.document_code == "110"
                and document.fiscal_state == "accepted"
            )[-1:]

    def action_create_note(self):
        """Create the draft note and open it for review and posting."""
        self.ensure_one()
        if not self.env.user.has_group("l10n_cl_dte_core.group_dte_manager"):
            raise AccessError(
                self.env._("Only a DTE manager can prepare export notes.")
            )
        self.move_id.check_access("write")
        if not self.target_document_id:
            raise UserError(
                self.env._("Export notes need an export 110 accepted by the SII.")
            )
        if self.document_code == "111" and self.correction != "3":
            raise UserError(
                self.env._("An export debit note can only correct amounts.")
            )
        note = self.env["account.move"].create(self._note_values())
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": note.id,
            "view_mode": "form",
        }

    def _note_values(self):
        move = self.move_id
        document_type = self.env["l10n_latam.document.type"].search(
            [("country_id.code", "=", "CL"), ("code", "=", self.document_code)],
            limit=1,
        )
        if not document_type:
            raise UserError(self.env._("The export note document type is missing."))
        text_only = self.correction == TEXT_CORRECTION
        return {
            "move_type": MOVE_TYPES[self.document_code],
            "partner_id": move.partner_id.id,
            "currency_id": move.currency_id.id,
            "journal_id": move.journal_id.id,
            "company_id": move.company_id.id,
            "invoice_date": fields.Date.context_today(self),
            "l10n_latam_document_type_id": document_type.id,
            "dte_export_target_id": self.target_document_id.id,
            "dte_export_correction": self.correction,
            "dte_export_reason": self.reason,
            "invoice_line_ids": [
                (
                    0,
                    0,
                    {
                        "product_id": line.product_id.id,
                        "name": line.name,
                        "quantity": line.quantity,
                        "product_uom_id": line.product_uom_id.id,
                        "price_unit": 0.0 if text_only else line.price_unit,
                        "tax_ids": [(6, 0, [])],
                    },
                )
                for line in move.invoice_line_ids.filtered(
                    lambda item: item.display_type == "product"
                )
            ],
        }
