# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ElectronicDocument(models.Model):
    _inherit = "l10n_cl.electronic.document"

    source_pos_order_id = fields.Many2one(
        comodel_name="pos.order",
        string="Source POS Order",
        index=True,
        ondelete="restrict",
        copy=False,
        check_company=True,
    )
