# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.exceptions import ValidationError

GUIDE_CODE = "52"


class DteInvoiceReference(models.Model):
    _inherit = "l10n_cl.dte.invoice.reference"

    dispatch_document_id = fields.Many2one(
        "l10n_cl.electronic.document",
        string="Delivery Guide",
        index=True,
        ondelete="restrict",
        domain=[
            ("document_code", "=", GUIDE_CODE),
            ("folio", "!=", False),
            ("fiscal_state", "!=", "rejected"),
        ],
        help="Issued delivery guide 52 this invoice refers to.",
    )

    @api.onchange("dispatch_document_id")
    def _onchange_dispatch_document_id(self):
        for reference in self.filtered("dispatch_document_id"):
            reference.update(reference._guide_identity(reference.dispatch_document_id))
            reference.reason = reference.reason or self.env._("Delivery guide")

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._with_guide_values(vals) for vals in vals_list])

    def write(self, vals):
        return super().write(self._with_guide_values(vals))

    def _with_guide_values(self, vals):
        """Take type, folio and date from the guide instead of typed values."""
        if not vals.get("dispatch_document_id"):
            return vals
        guide = self.env["l10n_cl.electronic.document"].browse(
            vals["dispatch_document_id"]
        )
        values = dict(vals, **self._guide_identity(guide))
        if not values.get("reason") and not self:
            values["reason"] = self.env._("Delivery guide")
        return values

    def _guide_identity(self, guide):
        guide = guide.sudo()
        guide_type = self.env["l10n_latam.document.type"].search(
            [("country_id.code", "=", "CL"), ("code", "=", GUIDE_CODE)], limit=1
        )
        return {
            "document_type_id": guide_type.id,
            "folio": str(guide.folio),
            "date": guide.source_dispatch_id.issue_date,
        }

    @api.constrains("dispatch_document_id", "move_id")
    def _check_dispatch_document(self):
        for reference in self.filtered("dispatch_document_id"):
            guide = reference.dispatch_document_id.sudo()
            move = reference.move_id
            dispatch = guide.source_dispatch_id
            if guide.document_code != GUIDE_CODE or not dispatch or not guide.folio:
                raise ValidationError(
                    self.env._("The reference must be an issued delivery guide 52.")
                )
            if guide.company_id != move.company_id or (
                dispatch.partner_id.commercial_partner_id
                != move.partner_id.commercial_partner_id
            ):
                raise ValidationError(
                    self.env._(
                        "The delivery guide belongs to another company or customer."
                    )
                )
