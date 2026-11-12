# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class DteDispatchReference(models.Model):
    _name = "l10n_cl.dte.dispatch.reference"
    _description = "Electronic dispatch guide reference"

    dispatch_id = fields.Many2one(
        "l10n_cl.dte.dispatch",
        required=True,
        ondelete="cascade",
        index=True,
    )
    reference_code = fields.Char(required=True)
    referenced_document_type = fields.Char()
    referenced_folio = fields.Char()
    reference_date = fields.Date()
    reason = fields.Char()
