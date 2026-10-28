# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import fields, models


class RcvSection(models.Model):
    _name = "l10n.cl.rcv.section"
    _description = "SII RCV synchronization section"
    _order = "sync_id, sii_state"
    _check_company_auto = True

    sync_id = fields.Many2one(
        "l10n.cl.rcv.sync", required=True, index=True, ondelete="cascade"
    )
    company_id = fields.Many2one(related="sync_id.company_id", store=True, index=True)
    operation = fields.Selection(related="sync_id.operation", store=True)
    sii_state = fields.Char(required=True, index=True)
    state = fields.Selection(
        [("complete", "Completa"), ("partial", "Parcial"), ("failed", "Fallida")],
        required=True,
        index=True,
    )
    document_type_codes = fields.Char(
        help="Códigos separados por coma informados por el SII."
    )
    expected_document_count = fields.Integer()
    observed_document_count = fields.Integer()
    error_message = fields.Char()

    _sql_constraints = [
        (
            "section_state_uniq",
            "unique(sync_id, sii_state)",
            "La sección ya existe en esta sincronización.",
        )
    ]
