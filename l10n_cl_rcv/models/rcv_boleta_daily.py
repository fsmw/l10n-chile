# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import fields, models


class RcvBoletaDaily(models.Model):
    _name = "l10n.cl.rcv.boleta.daily"
    _description = "SII daily receipt aggregate"
    _order = "day desc, document_type_code"
    _check_company_auto = True

    sync_id = fields.Many2one(
        "l10n.cl.rcv.sync", required=True, index=True, ondelete="cascade"
    )
    company_id = fields.Many2one(related="sync_id.company_id", store=True, index=True)
    day = fields.Date(required=True, index=True)
    document_type_code = fields.Char(required=True)
    document_count = fields.Integer()
    net_amount = fields.Monetary(currency_field="currency_id")
    exempt_amount = fields.Monetary(currency_field="currency_id")
    vat_amount = fields.Monetary(currency_field="currency_id")
    total_amount = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one(related="sync_id.company_id.currency_id")

    _sql_constraints = [
        (
            "daily_receipt_uniq",
            "unique(sync_id, day, document_type_code)",
            "El agregado diario ya existe en esta ejecución.",
        )
    ]
