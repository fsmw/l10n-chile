# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class DteInvoiceReference(models.Model):
    """A document the DTE cites: purchase order, HES, guide or another DTE."""

    _name = "l10n_cl.dte.invoice.reference"
    _description = "DTE Invoice Reference"
    _order = "move_id, sequence, id"

    move_id = fields.Many2one(
        "account.move", required=True, index=True, ondelete="cascade"
    )
    sequence = fields.Integer(default=10)
    document_type_id = fields.Many2one(
        "l10n_latam.document.type",
        string="Document Type",
        required=True,
        domain="[('country_id.code', '=', 'CL')]",
        help="SII document type of the cited document (TpoDocRef).",
    )
    folio = fields.Char(required=True, size=18, help="Cited document number.")
    date = fields.Date(required=True)
    reason = fields.Char(required=True, size=90)

    _sql_constraints = [
        (
            "folio_not_blank",
            "CHECK (length(trim(folio)) > 0)",
            "The cited document number is required.",
        )
    ]

    @api.model_create_multi
    def create(self, vals_list):
        moves = self.env["account.move"].browse(
            [vals.get("move_id") for vals in vals_list]
        )
        self._check_moves_editable(moves)
        return super().create(vals_list)

    def write(self, vals):
        self._check_moves_editable(self.move_id)
        if "move_id" in vals:
            self._check_moves_editable(self.env["account.move"].browse(vals["move_id"]))
        return super().write(vals)

    def unlink(self):
        self._check_moves_editable(self.move_id)
        return super().unlink()

    @staticmethod
    def _check_moves_editable(moves):
        if any(move._has_binding_dte() for move in moves.exists()):
            raise UserError(
                _("References cannot change after the DTE has been issued.")
            )

    def _dte_snapshot(self, start=1):
        """Serialize references in the composer's Referencia contract."""
        return [
            {
                "line_number": index,
                "document_type": reference.document_type_id.code,
                "folio": reference.folio.strip(),
                "date": reference.date.isoformat(),
                "reason": reference.reason.strip(),
            }
            for index, reference in enumerate(self, start=start)
        ]
