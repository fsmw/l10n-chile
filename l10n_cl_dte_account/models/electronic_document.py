from odoo import fields, models


class ElectronicDocument(models.Model):
    _inherit = "l10n_cl.electronic.document"

    source_move_id = fields.Many2one(
        "account.move", index=True, ondelete="restrict", copy=False, check_company=True
    )
