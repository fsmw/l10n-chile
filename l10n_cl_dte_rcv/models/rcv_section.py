# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class RcvSection(models.Model):
    """Coverage of one SII state (REGISTRO, PENDIENTE...) within a read."""

    _name = "l10n_cl.dte.rcv.section"
    _description = "SII RCV Read Section"
    _order = "sync_id, sii_state"
    _check_company_auto = True

    sync_id = fields.Many2one(
        "l10n_cl.dte.rcv.sync", required=True, index=True, ondelete="cascade"
    )
    company_id = fields.Many2one(related="sync_id.company_id", store=True, index=True)
    operation = fields.Selection(related="sync_id.operation", store=True)
    sii_state = fields.Char(string="SII State", required=True, index=True)
    state = fields.Selection(
        [("complete", "Complete"), ("partial", "Partial"), ("failed", "Failed")],
        required=True,
        index=True,
    )
    document_type_codes = fields.Char(
        help="Comma-separated document type codes reported by the SII."
    )
    expected_document_count = fields.Integer()
    observed_document_count = fields.Integer()
    error_message = fields.Char()

    _sql_constraints = [
        (
            "section_state_uniq",
            "unique(sync_id, sii_state)",
            "This section already exists in the read.",
        )
    ]
