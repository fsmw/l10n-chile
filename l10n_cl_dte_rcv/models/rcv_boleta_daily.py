# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class RcvBoletaDaily(models.Model):
    """Daily receipt (boleta) totals that the SII reports for a read."""

    _name = "l10n_cl.dte.rcv.boleta.daily"
    _description = "SII RCV Daily Receipt Totals"
    _order = "day desc, document_type_code"
    _check_company_auto = True

    sync_id = fields.Many2one(
        "l10n_cl.dte.rcv.sync", required=True, index=True, ondelete="cascade"
    )
    company_id = fields.Many2one(related="sync_id.company_id", store=True, index=True)
    day = fields.Date(required=True, index=True)
    document_type_code = fields.Char(required=True)
    document_count = fields.Integer()
    net_amount = fields.Monetary(currency_field="currency_id")
    exempt_amount = fields.Monetary(currency_field="currency_id")
    vat_amount = fields.Monetary(string="VAT Amount", currency_field="currency_id")
    total_amount = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one(related="sync_id.company_id.currency_id")

    _sql_constraints = [
        (
            "daily_receipt_uniq",
            "unique(sync_id, day, document_type_code)",
            "These daily receipt totals already exist in the read.",
        )
    ]
