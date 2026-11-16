from odoo import _, fields, models
from odoo.exceptions import UserError


class ElectronicDocument(models.Model):
    _inherit = "l10n_cl.electronic.document"

    source_move_id = fields.Many2one(
        "account.move", index=True, ondelete="restrict", copy=False, check_company=True
    )

    def write(self, vals):
        if "source_move_id" in vals:
            raise UserError(_("The DTE accounting source is immutable after creation."))
        return super().write(vals)
