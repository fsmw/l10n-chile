# Copyright 2026 SMWLAB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import fields, models


class RcvSummary(models.Model):
    _name = "l10n.cl.rcv.summary"
    _description = "SII RCV summary row"
    _order = "sync_id, sii_state, document_type_code"
    _check_company_auto = True

    sync_id = fields.Many2one(
        "l10n.cl.rcv.sync", required=True, index=True, ondelete="cascade"
    )
    company_id = fields.Many2one(related="sync_id.company_id", store=True, index=True)
    operation = fields.Selection(related="sync_id.operation", store=True)
    sii_state = fields.Char(index=True)
    document_type_code = fields.Char(required=True, index=True)
    document_type_name = fields.Char()
    document_count = fields.Integer()
    net_amount = fields.Monetary(currency_field="currency_id")
    exempt_amount = fields.Monetary(currency_field="currency_id")
    vat_amount = fields.Monetary(currency_field="currency_id")
    vat_not_recoverable = fields.Monetary(currency_field="currency_id")
    common_vat = fields.Monetary(currency_field="currency_id")
    total_amount = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one(related="sync_id.company_id.currency_id")
