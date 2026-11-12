# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ElectronicDocument(models.Model):
    _inherit = "l10n_cl.electronic.document"

    source_dispatch_id = fields.Many2one(
        "l10n_cl.dte.dispatch",
        index=True,
        ondelete="restrict",
        copy=False,
        check_company=True,
    )
